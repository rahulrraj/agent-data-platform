"""discover_capability: semantic search over the capability registry.

Given a natural-language need ("customer tier lookup"), returns the matching
capabilities with their parameter schemas. The model then calls
execute_capability with the chosen id.

Factory plugin: create_tool(deps) needs deps.capability_registry and
deps.embedding_gateway. Descriptions are embedded once at wiring time.
"""

from __future__ import annotations

import json

from strands import tool

from agent_api.domain.capability import cosine_similarity

_SIMILARITY_THRESHOLD = 0.30  # below this, report no match rather than guess


def create_tool(deps):
    registry = deps.capability_registry
    embedding_gateway = deps.embedding_gateway
    if registry is None or embedding_gateway is None:
        raise RuntimeError(
            "discover_capability needs capability_registry and embedding_gateway"
        )

    capabilities = registry.list_all()
    vectors = (
        embedding_gateway.embed([c.description for c in capabilities])
        if capabilities
        else []
    )
    index = list(zip(capabilities, vectors))

    @tool
    def discover_capability(need: str) -> str:
        """Find which organization data you can look up for a need.

        Describe what you need in plain words, e.g. "customer tier by ID".
        Returns matching capabilities with their ids and parameter schemas —
        pass the chosen id and parameters to execute_capability.
        """
        if not index:
            return "No data capabilities are configured."
        query_vector = embedding_gateway.embed([need])[0]
        ranked = sorted(
            index,
            key=lambda item: cosine_similarity(query_vector, item[1]),
            reverse=True,
        )
        matches = [
            {
                "id": cap.id,
                "description": cap.description,
                "parameters": {
                    p.name: {
                        "type": p.type,
                        "required": p.required,
                        "description": p.description,
                    }
                    for p in cap.parameters
                },
            }
            for cap, vector in ranked[:3]
            if cosine_similarity(query_vector, vector) >= _SIMILARITY_THRESHOLD
        ]
        if not matches:
            return (
                "No data capability matches that need. "
                "Answer from what you know or say you cannot look it up."
            )
        return json.dumps(matches)

    return discover_capability
