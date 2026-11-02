"""Discovery calibration (M-12): threshold + margin rules checked against
real Gemini embeddings, recorded — not live — so the suite stays offline.

How the table was produced (2026-10-25): each capability's indexed text
(description + parameter descriptions, exactly as
tools/discover_capability/tool.py builds it) and each prompt below were
embedded with gemini-embedding-001 via the custom.gemini connector
helper, and cosine similarities were recorded. Vectors themselves are
not stored (3072 floats each); the similarities are what the rules
decide on, so the table is the faithful record.

What the table shows:
- Unrelated prompts score up to 0.5258 — the old 0.30 threshold would
  have admitted them as matches. Weakest true paraphrase: 0.6299.
  Threshold 0.60 separates the two groups with room on both sides.
- True matches have margins >= 0.061; ambiguous near-misses fall at
  <= 0.039. Margin δ = 0.05 separates them.
- Caveat: these constants are calibrated for gemini-embedding-001 on
  this registry. Another embedding provider's score scale differs and
  would need its own calibration pass before trusting the same numbers.
  The threshold is therefore per-model (declared by each embedding
  gateway); Titan v2's 0.30 is provisional — a single observed true
  question, no false-positive calibration — and is not part of this
  table.
"""

from __future__ import annotations

import json
import math

import pytest

from agent_api.adapters.gemini_embedding_gateway import GeminiEmbeddingGateway
from agent_api.adapters.ollama_embedding_gateway import OllamaEmbeddingGateway
from agent_api.domain.capability import Capability, ParameterSpec
from agent_api.ports.embedding_gateway import DEFAULT_SIMILARITY_THRESHOLD
from agent_api.tools import ToolDeps
from agent_api.tools.discover_capability import tool as discover_module
from agent_api.tools.discover_capability.tool import create_tool as create_discover

# prompt -> {capability_id: cosine similarity}
_MEASURED = {
    "What tier is customer C-123?": {"accounts_by_customer": 0.6703, "customer_by_id": 0.7667, "order_status": 0.5948, "orders_by_customer": 0.6771, "returns_by_customer": 0.6532},
    "Show me all accounts belonging to customer C-123": {"accounts_by_customer": 0.8725, "customer_by_id": 0.7223, "order_status": 0.6182, "orders_by_customer": 0.7572, "returns_by_customer": 0.7747},
    "What are the returns for all accounts of customer C-123?": {"accounts_by_customer": 0.7839, "customer_by_id": 0.7035, "order_status": 0.6221, "orders_by_customer": 0.7306, "returns_by_customer": 0.8878},
    "List orders placed by customer C-123": {"accounts_by_customer": 0.747, "customer_by_id": 0.7144, "order_status": 0.7188, "orders_by_customer": 0.8947, "returns_by_customer": 0.7005},
    "Where is my order O-789?": {"accounts_by_customer": 0.5568, "customer_by_id": 0.565, "order_status": 0.7852, "orders_by_customer": 0.6397, "returns_by_customer": 0.5403},
    "Is this customer gold or platinum?": {"accounts_by_customer": 0.5917, "customer_by_id": 0.6644, "order_status": 0.5767, "orders_by_customer": 0.5882, "returns_by_customer": 0.5984},
    "How much money did my investments make?": {"accounts_by_customer": 0.5426, "customer_by_id": 0.5192, "order_status": 0.546, "orders_by_customer": 0.5252, "returns_by_customer": 0.7122},
    "Track my package": {"accounts_by_customer": 0.514, "customer_by_id": 0.5515, "order_status": 0.6915, "orders_by_customer": 0.5664, "returns_by_customer": 0.5206},
    "What level is this client?": {"accounts_by_customer": 0.5634, "customer_by_id": 0.6299, "order_status": 0.5689, "orders_by_customer": 0.5467, "returns_by_customer": 0.5645},
    "What are the returns for customer C-123?": {"accounts_by_customer": 0.7059, "customer_by_id": 0.7109, "order_status": 0.6714, "orders_by_customer": 0.7534, "returns_by_customer": 0.7928},
    "List all accounts and their returns": {"accounts_by_customer": 0.7376, "customer_by_id": 0.6155, "order_status": 0.5858, "orders_by_customer": 0.6047, "returns_by_customer": 0.7947},
    "Show me orders and their status": {"accounts_by_customer": 0.6166, "customer_by_id": 0.591, "order_status": 0.746, "orders_by_customer": 0.7247, "returns_by_customer": 0.5872},
    "What is the weather tomorrow?": {"accounts_by_customer": 0.4669, "customer_by_id": 0.4996, "order_status": 0.5167, "orders_by_customer": 0.4804, "returns_by_customer": 0.4891},
    "Tell me a joke": {"accounts_by_customer": 0.498, "customer_by_id": 0.4999, "order_status": 0.4866, "orders_by_customer": 0.5015, "returns_by_customer": 0.4756},
    "How do I reset my password?": {"accounts_by_customer": 0.5258, "customer_by_id": 0.5145, "order_status": 0.5118, "orders_by_customer": 0.5098, "returns_by_customer": 0.5002},
}

# prompt -> expected classification under threshold 0.60 / margin 0.05
_EXPECTED = {
    "What tier is customer C-123?": ("single", "customer_by_id"),
    "Show me all accounts belonging to customer C-123": ("single", "accounts_by_customer"),
    "What are the returns for all accounts of customer C-123?": ("single", "returns_by_customer"),
    "List orders placed by customer C-123": ("single", "orders_by_customer"),
    "Where is my order O-789?": ("single", "order_status"),
    "Is this customer gold or platinum?": ("single", "customer_by_id"),
    "How much money did my investments make?": ("single", "returns_by_customer"),
    "Track my package": ("single", "order_status"),
    "What level is this client?": ("single", "customer_by_id"),
    "What are the returns for customer C-123?": ("ambiguous", None),
    "List all accounts and their returns": ("single", "returns_by_customer"),
    "Show me orders and their status": ("ambiguous", None),
    "What is the weather tomorrow?": ("none", None),
    "Tell me a joke": ("none", None),
    "How do I reset my password?": ("none", None),
}


def _classify(sims: dict[str, float]) -> tuple[str, str | None]:
    # Gemini path: the recorded similarities were measured with
    # gemini-embedding-001, whose per-model threshold is the default.
    ranked = sorted(sims.items(), key=lambda kv: -kv[1])
    best, best_score = ranked[0]
    if best_score < DEFAULT_SIMILARITY_THRESHOLD:
        return "none", None
    if best_score - ranked[1][1] <= discover_module._MARGIN:
        return "ambiguous", None
    return "single", best


def test_calibrated_constants():
    # Gemini path: the 0.60 threshold and δ = 0.05 margin still hold.
    assert DEFAULT_SIMILARITY_THRESHOLD == 0.60
    assert discover_module._MARGIN == 0.05
    assert GeminiEmbeddingGateway("key").similarity_threshold == 0.60
    assert (
        OllamaEmbeddingGateway("http://localhost:11434").similarity_threshold
        == 0.60
    )


@pytest.mark.parametrize("prompt", sorted(_MEASURED))
def test_recorded_similarities_classify_as_expected(prompt):
    kind, best = _classify(_MEASURED[prompt])
    assert (kind, best) == _EXPECTED[prompt], prompt


def test_unrelated_prompts_would_have_passed_old_threshold():
    # The regression the recalibration fixes: under 0.30, every unrelated
    # prompt below looked like a match.
    for prompt in (
        "What is the weather tomorrow?",
        "Tell me a joke",
        "How do I reset my password?",
    ):
        assert max(_MEASURED[prompt].values()) > 0.30
        assert max(_MEASURED[prompt].values()) < 0.60


# --- Per-model threshold: the gateway declares the cutoff ---


class _ThresholdEmbeddings:
    """One capability + one query at cosine similarity 0.35: between
    the Titan cutoff (0.30) and the default cutoff (0.60)."""

    model_name = "threshold-test"

    def __init__(self, threshold: float | None):
        # None = legacy gateway predating the port extension: no
        # similarity_threshold attribute at all.
        if threshold is not None:
            self.similarity_threshold = threshold

    def embed(self, texts):
        vectors = {
            "alpha capability": [1.0, 0.0],
            "need": [0.35, math.sqrt(1 - 0.35**2)],
        }
        return [vectors[t] for t in texts]

    def ping(self):
        return None


def _threshold_tool(threshold: float | None):
    deps = ToolDeps(
        capability_registry=_FakeRegistry([_cap("a", "alpha capability")]),
        embedding_gateway=_ThresholdEmbeddings(threshold),
    )
    return create_discover(deps)


@pytest.mark.parametrize(
    "threshold,expected_id",
    [(0.30, "a"), (0.60, None)],
)
def test_gateway_threshold_decides_match(threshold, expected_id):
    result = _threshold_tool(threshold)("need")
    if expected_id is None:
        assert "No data capability matches" in result
    else:
        assert json.loads(result)["id"] == expected_id


def test_gateway_without_threshold_falls_back_to_default():
    # A 0.35 score must not match under the 0.60 default.
    result = _threshold_tool(None)("need")
    assert "No data capability matches" in result


# --- Functional margin / indexed-text behavior with a fake embedding ---


class _FakeRegistry:
    def __init__(self, caps):
        self._caps = {c.id: c for c in caps}

    def list_all(self):
        return list(self._caps.values())

    def get(self, capability_id):
        return self._caps.get(capability_id)

    def ping(self):
        return None


class _VectorEmbeddings:
    """Returns caller-supplied vectors keyed by exact text."""

    model_name = "test-vectors"
    similarity_threshold = 0.60

    def __init__(self, vectors):
        self._vectors = vectors
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [self._vectors[t] for t in texts]

    def ping(self):
        return None


def _cap(cid, description, param_desc=""):
    params = (
        (ParameterSpec(name="p", type="string", required=True, description=param_desc),)
        if param_desc
        else ()
    )
    return Capability(id=cid, description=description, kind="http",
                      connection="c", mode="read", parameters=params, spec={})


def test_margin_returns_top_two_with_scores():
    caps = [_cap("a", "alpha capability"), _cap("b", "beta capability")]
    vectors = {
        "alpha capability": [1.0, 0.0],
        "beta capability": [0.98, 0.199],
        "need": [1.0, 0.0],
    }
    deps = ToolDeps(
        capability_registry=_FakeRegistry(caps),
        embedding_gateway=_VectorEmbeddings(vectors),
    )
    out = json.loads(create_discover(deps)("need"))
    assert out["ambiguous"] is True
    assert [c["id"] for c in out["candidates"]] == ["a", "b"]
    assert out["candidates"][0]["score"] > out["candidates"][1]["score"]


def test_clear_winner_returns_single_with_score():
    caps = [_cap("a", "alpha capability"), _cap("b", "beta capability")]
    vectors = {
        "alpha capability": [1.0, 0.0],
        "beta capability": [0.0, 1.0],
        "need": [1.0, 0.0],
    }
    deps = ToolDeps(
        capability_registry=_FakeRegistry(caps),
        embedding_gateway=_VectorEmbeddings(vectors),
    )
    out = json.loads(create_discover(deps)("need"))
    assert out["id"] == "a"
    assert out["score"] == 1.0


def test_indexed_text_includes_parameter_descriptions():
    caps = [_cap("a", "customer lookup", "Customer ID, e.g. C-123")]
    embeddings = _VectorEmbeddings(
        {
            "customer lookup\np: Customer ID, e.g. C-123": [1.0, 0.0],
            "need": [1.0, 0.0],
        }
    )
    deps = ToolDeps(capability_registry=_FakeRegistry(caps), embedding_gateway=embeddings)
    out = json.loads(create_discover(deps)("need"))
    assert out["id"] == "a"
    # First embed call is the indexing batch (before the query itself).
    assert embeddings.calls[0] == ["customer lookup\np: Customer ID, e.g. C-123"]


def test_fallback_strings_refuse_from_memory():
    class _BrokenEmbeddings:
        model_name = "broken"
        similarity_threshold = 0.60

        def embed(self, texts):
            raise RuntimeError("down")

        def ping(self):
            return None

    deps = ToolDeps(
        capability_registry=_FakeRegistry([_cap("a", "alpha")]),
        embedding_gateway=_BrokenEmbeddings(),
    )
    result = create_discover(deps)("need")
    assert "say you cannot look it up" in result.lower()
    assert "Answer from what you know" not in result
