"""discover_capability: semantic search over the capability registry.

Given a natural-language need ("customer tier lookup"), returns the
best-matching capability with its parameter schema. The model then calls
execute_capability with the chosen id.

Factory plugin: create_tool(deps) needs deps.capability_registry and
deps.embedding_gateway; deps.embedding_cache is optional. Indexed texts
are embedded once and cached: in memory per process, plus in the cache
across restarts — only new or edited texts are re-embedded.

Calibration (gemini-embedding-001, 2026-10-25, 15 labeled prompts over
the 5-capability fixture registry — paraphrases, near-miss distractors,
unrelated prompts; see tests/tools/test_discovery_calibration.py):

- Unrelated prompts score up to 0.53; the weakest true paraphrase
  scores 0.63. The old 0.30 threshold admitted unrelated prompts as
  matches in dense-embedding space, so the threshold is 0.60.
- True matches have best-minus-second-best margins >= 0.061; the
  ambiguous near-misses fall at <= 0.039. The margin rule is δ = 0.05:
  within δ, both top candidates are returned with scores and the model
  picks (ADR 0004's single-best-match is relaxed exactly here — a
  forced single answer on a near-tie is a guess, not a decision).
- Indexed text is description + parameter descriptions, not the
  description alone: phrasings that use parameter synonyms (order
  number, tracking) then match on the parameter text too.

Fallbacks never invite answering from memory: every failure/no-match
message ends with "say you cannot look it up".
"""

from __future__ import annotations

import hashlib
import json

from strands import tool

from agent_api.domain.capability import cosine_similarity
from agent_api.ports.embedding_cache import CachedEmbedding

_SIMILARITY_THRESHOLD = 0.60  # below this, report no match rather than guess
_MARGIN = 0.05  # best minus second-best <= this -> return top 2 with scores


def _index_text(capability) -> str:
    """Text actually embedded for a capability: description plus the
    description of each parameter that has one."""
    lines = [capability.description]
    for p in capability.parameters:
        if p.description:
            lines.append(f"{p.name}: {p.description}")
    return "\n".join(lines)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _capability_json(capability, score: float | None = None) -> dict:
    payload = {
        "id": capability.id,
        "description": capability.description,
        "parameters": {
            p.name: {
                "type": p.type,
                "required": p.required,
                "description": p.description,
            }
            for p in capability.parameters
        },
    }
    if score is not None:
        payload["score"] = round(score, 4)
    return payload


def create_tool(deps):
    registry = deps.capability_registry
    embedding_gateway = deps.embedding_gateway
    embedding_cache = deps.embedding_cache
    discovered = getattr(deps, "discovered_capability_ids", None)
    if registry is None or embedding_gateway is None:
        raise RuntimeError(
            "discover_capability needs capability_registry and embedding_gateway"
        )

    # Indexed texts are embedded lazily on first use and cached: startup
    # must never block on the embedding backend (it may be down or
    # missing the model). A failed lookup returns a clean message and
    # retries next call. The persistent cache (if wired) survives
    # restarts; the in-memory dict covers the process lifetime. The
    # cache key is the hash of the indexed text, so editing a parameter
    # description invalidates that capability's vector.
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
                if hit is not None and hit.description_hash == _text_hash(_index_text(cap)):
                    hits[cap.id] = list(hit.vector)
                else:
                    pending.append(cap)
            if pending:
                # May raise: the caller turns it into "temporarily unavailable".
                fresh = embedding_gateway.embed([_index_text(c) for c in pending])
                if embedding_cache is not None:
                    try:
                        embedding_cache.save(
                            [
                                CachedEmbedding(
                                    capability_id=c.id,
                                    description_hash=_text_hash(_index_text(c)),
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
        Returns the best-matching capability as JSON with its id, score,
        and parameter schema — pass the id and parameters to
        execute_capability. If two capabilities are within the margin,
        both are returned with scores; if nothing matches, it says so
        instead of guessing.
        """
        try:
            index = _index()
        except Exception as exc:  # noqa: BLE001
            return (
                "Data capability lookup is temporarily unavailable: "
                f"{exc}. Say you cannot look it up."
            )
        if not index:
            return "No data capabilities are configured."
        try:
            query_vector = embedding_gateway.embed([need])[0]
        except Exception as exc:  # noqa: BLE001
            return (
                "Data capability lookup is temporarily unavailable: "
                f"{exc}. Say you cannot look it up."
            )
        scored = sorted(
            (
                (cap, cosine_similarity(query_vector, vector))
                for cap, vector in index
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        best, best_score = scored[0]
        if best_score < _SIMILARITY_THRESHOLD:
            return "No data capability matches that need. Say you cannot look it up."
        if discovered is not None:
            discovered.add(best.id)
        if len(scored) > 1 and best_score - scored[1][1] <= _MARGIN:
            # Near-tie: return both candidates with scores rather than
            # forcing a single (possibly wrong) answer.
            second, second_score = scored[1]
            if discovered is not None:
                discovered.add(second.id)
            return json.dumps(
                {
                    "ambiguous": True,
                    "candidates": [
                        _capability_json(best, best_score),
                        _capability_json(second, second_score),
                    ],
                }
            )
        return json.dumps(_capability_json(best, best_score))

    return discover_capability
