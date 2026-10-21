"""discover_capability: semantic search over the capability registry.

Given a natural-language need ("customer tier lookup"), returns the single
best-matching capability with its parameter schema. The model then calls
execute_capability with the chosen id.

Factory plugin: create_tool(deps) needs deps.capability_registry and
deps.embedding_gateway; deps.embedding_cache is optional. Descriptions are
embedded once and cached: in memory per process, plus in the cache across
restarts — only new or edited descriptions are re-embedded.
"""

from __future__ import annotations

import hashlib
import json

from strands import tool

from agent_api.domain.capability import cosine_similarity
from agent_api.ports.embedding_cache import CachedEmbedding

_SIMILARITY_THRESHOLD = 0.30  # below this, report no match rather than guess


def _description_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def create_tool(deps):
    registry = deps.capability_registry
    embedding_gateway = deps.embedding_gateway
    embedding_cache = deps.embedding_cache
    if registry is None or embedding_gateway is None:
        raise RuntimeError(
            "discover_capability needs capability_registry and embedding_gateway"
        )

    # Descriptions are embedded lazily on first use and cached: startup must
    # never block on the embedding backend (it may be down or missing the
    # model). A failed lookup returns a clean message and retries next call.
    # The persistent cache (if wired) survives restarts; the in-memory dict
    # covers the process lifetime.
    cache: dict = {}

    def _index():
        if "vectors" not in cache:
            capabilities = registry.list_all()
            model = embedding_gateway.model_name
            stored: dict = {}
            if embedding_cache is not None:
                try:
                    stored = embedding_cache.load(model)
                except Exception:  # noqa: BLE001 — cache is best-effort
                    stored = {}
            hits: dict[str, list[float]] = {}
            pending: list = []
            for cap in capabilities:
                hit = stored.get(cap.id)
                if hit is not None and hit.description_hash == _description_hash(cap.description):
                    hits[cap.id] = list(hit.vector)
                else:
                    pending.append(cap)
            if pending:
                # May raise: the caller turns it into "temporarily unavailable".
                fresh = embedding_gateway.embed([c.description for c in pending])
                if embedding_cache is not None:
                    try:
                        embedding_cache.save(
                            [
                                CachedEmbedding(
                                    capability_id=c.id,
                                    description_hash=_description_hash(c.description),
                                    model=model,
                                    vector=tuple(v),
                                )
                                for c, v in zip(pending, fresh)
                            ]
                        )
                    except Exception:  # noqa: BLE001 — cache is best-effort
                        pass
                for cap, vector in zip(pending, fresh):
                    hits[cap.id] = vector
            cache["vectors"] = [(cap, hits[cap.id]) for cap in capabilities]
        return cache["vectors"]

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
