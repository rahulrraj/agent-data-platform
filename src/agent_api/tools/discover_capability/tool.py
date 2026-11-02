"""discover_capability: semantic search over the capability registry.

Given a natural-language need ("customer tier lookup"), returns the
best-matching capability with its parameter schema. The model then calls
execute_capability with the chosen id.

Factory plugin: create_tool(deps) needs deps.capability_registry and
deps.embedding_gateway; deps.embedding_cache is optional. Indexed texts
are embedded once and cached: in memory per process, plus in the cache
across restarts — only new or edited texts are re-embedded.

The similarity threshold is per-model, not a single constant:
cosine-similarity scales differ between embedding models, so the
cutoff lives on the EmbeddingGateway port
(`similarity_threshold`), declared by each adapter for its own
model. The tool never branches on a provider or model name, and the
composition root / Settings are not involved. The margin rule below
is model-independent and unchanged.

Calibration (gemini-embedding-001, 2026-10-25, 15 labeled prompts over
the 5-capability fixture registry — paraphrases, near-miss distractors,
unrelated prompts; see tests/tools/test_discovery_calibration.py):

- Unrelated prompts score up to 0.53; the weakest true paraphrase
  scores 0.63. The old 0.30 threshold admitted unrelated prompts as
  matches in dense-embedding space, so the threshold is 0.60 for
  this model.
- True matches have best-minus-second-best margins >= 0.061; the
  ambiguous near-misses fall at <= 0.039. The margin rule is δ = 0.05:
  within δ, both top candidates are returned with scores and the model
  picks (ADR 0004's single-best-match is relaxed exactly here — a
  forced single answer on a near-tie is a guess, not a decision).
- Indexed text is description + parameter descriptions, not the
  description alone: phrasings that use parameter synonyms (order
  number, tracking) then match on the parameter text too.

Titan (Bedrock amazon.titan-embed-text-v2:0) uses 0.30 — PROVISIONAL:
that value comes from a SINGLE observed true question scoring exactly
0.30, with no margin and no false-positive calibration (no
unrelated/near-miss prompts measured for this model). A Titan
calibration run using the same 15-prompt protocol as the Gemini
calibration is still needed before trusting 0.30; see the comment at
the value in adapters/bedrock_embedding_gateway.py. Every other
model keeps the 0.60 default until it gets its own calibration.

Fallbacks never invite answering from memory: every failure/no-match
message ends with "say you cannot look it up".
"""

from __future__ import annotations

import hashlib
import json
import threading

from strands import tool

from agent_api.domain.capability import cosine_similarity
from agent_api.ports.embedding_cache import CachedEmbedding
from agent_api.ports.embedding_gateway import DEFAULT_SIMILARITY_THRESHOLD

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
    # Per-model cutoff, declared by the gateway (see module docstring).
    # Gateways predating the port extension may not declare one; fall
    # back to the default rather than failing discovery.
    similarity_threshold = getattr(
        embedding_gateway, "similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD
    )
    if similarity_threshold is None:
        similarity_threshold = DEFAULT_SIMILARITY_THRESHOLD

    # Indexed texts are embedded lazily on first use and cached: startup
    # must never block on the embedding backend (it may be down or
    # missing the model). A failed lookup returns a clean message and
    # retries next call. The persistent cache (if wired) survives
    # restarts; the in-memory dict covers the process lifetime. The
    # cache key is the hash of the indexed text, so editing a parameter
    # description invalidates that capability's vector.
    cache: dict = {}
    index_lock = threading.Lock()

    def _index():
        # m-16c: the in-memory index used to be built without a lock,
        # so concurrent first discoveries duplicated the embed work.
        if "vectors" in cache:
            return cache["vectors"]
        with index_lock:
            if "vectors" in cache:
                return cache["vectors"]
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
                # m-16a: a stored row whose recorded dimension disagrees
                # with its vector length is corrupt — re-embed it, don't
                # compare it. (Same-name model changes that keep the
                # model string but change dimensions are caught below
                # by the cross-vector dimension check.)
                if (
                    hit is not None
                    and hit.description_hash == _text_hash(_index_text(cap))
                    and (
                        getattr(hit, "vector_dim", None) is None
                        or hit.vector_dim == len(hit.vector)
                    )
                ):
                    hits[cap.id] = list(hit.vector)
                else:
                    pending.append(cap)
            if len({len(v) for v in hits.values()}) > 1:
                # Cached vectors disagree about dimensions (e.g. the
                # embedding model changed under the same model string):
                # no ranking over them is meaningful. Drop the lot and
                # re-embed everything rather than zip-truncating.
                hits = {}
                pending = list(capabilities)
            if pending:
                # May raise: the caller turns it into "temporarily unavailable".
                fresh = embedding_gateway.embed([_index_text(c) for c in pending])
                # m-16b: a short embed result used to zip-drop
                # capabilities silently, then hits[cap.id] raised
                # KeyError on every discovery until restart. Fail
                # loudly instead — the caller reports unavailability.
                if len(fresh) != len(pending):
                    raise RuntimeError(
                        f"embedding gateway returned {len(fresh)} vectors "
                        f"for {len(pending)} texts"
                    )
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
                    # m-16c: drop rows for models no longer in use
                    # instead of accumulating them forever.
                    prune = getattr(embedding_cache, "prune_models", None)
                    if callable(prune):
                        try:
                            prune({model})
                        except Exception:  # noqa: BLE001 — best-effort
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
        try:
            scored = sorted(
                (
                    (cap, cosine_similarity(query_vector, vector))
                    for cap, vector in index
                ),
                key=lambda item: item[1],
                reverse=True,
            )
        except ValueError:
            # m-16a: the query vector's dimension disagrees with the
            # index (embedding model changed). Drop the stale index so
            # the next call re-embeds, and report unavailability now
            # instead of a wrong ranking.
            cache.pop("vectors", None)
            return (
                "Data capability lookup is temporarily unavailable: "
                "embedding dimension mismatch. Say you cannot look it up."
            )
        best, best_score = scored[0]
        if best_score < similarity_threshold:
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
