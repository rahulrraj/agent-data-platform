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
    # Descriptions are embedded lazily on first use and cached: startup must
    # never block on the embedding backend (it may be down or missing the
    # model). A failed lookup returns a clean message and retries next call.
    cache: dict = {}

    def _index():
        if "vectors" not in cache:
            cache["vectors"] = (
                embedding_gateway.embed([c.description for c in capabilities])
                if capabilities
                else []
            )
        return list(zip(capabilities, cache["vectors"]))

    @tool
    def discover_capability(need: str) -> str:
        """Find the organization-data API for a need.

        Describe what you need in plain words, e.g. "customer tier by ID".
        Returns the single best-matching capability as JSON with its id and
        parameter schema — pass the id and parameters to execute_capability.
        If nothing matches, it says so instead of guessing.
        """
        try:
            index = _index()
        except Exception as exc:  # noqa: BLE001
            return (
                "Data capability lookup is temporarily unavailable: "
                f"{exc}. Answer from what you know."
            )
        if not index:
            return "No data capabilities are configured."
        try:
            query_vector = embedding_gateway.embed([need])[0]
        except Exception as exc:  # noqa: BLE001
            return (
                "Data capability lookup is temporarily unavailable: "
                f"{exc}. Answer from what you know."
            )
        ranked = sorted(
            index,
            key=lambda item: cosine_similarity(query_vector, item[1]),
            reverse=True,
        )
        # The single best match only (ADR 0004): discovery answers "which
        # API", not "which APIs". Below the threshold it reports no match
        # rather than guessing.
        best, best_vector = ranked[0]
        if cosine_similarity(query_vector, best_vector) < _SIMILARITY_THRESHOLD:
            return (
                "No data capability matches that need. "
                "Answer from what you know or say you cannot look it up."
            )
        return json.dumps(
            {
                "id": best.id,
                "description": best.description,
                "parameters": {
                    p.name: {
                        "type": p.type,
                        "required": p.required,
                        "description": p.description,
                    }
                    for p in best.parameters
                },
            }
        )

    return discover_capability
