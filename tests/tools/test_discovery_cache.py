"""Tool-level tests for the discovery embedding cache.

Simulates restarts by calling create_tool again with a fresh in-memory
closure: the persistent cache must prevent re-embedding unchanged
descriptions, and force re-embedding of new/edited ones or a new model.
"""

from __future__ import annotations

import json

from agent_api.adapters.sqlite_embedding_cache import SqliteEmbeddingCache
from agent_api.tools import ToolDeps
from agent_api.tools.discover_capability.tool import create_tool as create_discover


class FakeEmbeddings:
    model_name = "test-model"
    similarity_threshold = 0.60

    def __init__(self):
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(list(texts))
        # direction depends on the text: "customer" texts align with dim 0
        return [[float(t.lower().count("customer")), float(t.lower().count("order"))]
                for t in texts]

    def ping(self):
        return None


class FakeRegistry:
    def __init__(self, descriptions: dict[str, str]):
        self._descriptions = descriptions

    def list_all(self):
        from agent_api.domain.capability import Capability

        return [
            Capability(id=cid, description=desc, kind="http",
                       connection="c", mode="read", parameters=(), spec={})
            for cid, desc in self._descriptions.items()
        ]

    def get(self, capability_id):
        return None

    def ping(self):
        return None


def _deps(registry, embeddings, cache):
    return ToolDeps(
        capability_registry=registry,
        embedding_gateway=embeddings,
        embedding_cache=cache,
        capability_executors={},
    )


def test_second_startup_reuses_cache(tmp_path):
    cache = SqliteEmbeddingCache(str(tmp_path / "emb.db"))
    embeddings = FakeEmbeddings()
    registry = FakeRegistry({"a": "customer lookup", "b": "order lookup"})

    first = create_discover(_deps(registry, embeddings, cache))
    assert json.loads(first("customer"))["id"] == "a"
    # calls[0] = description indexing batch, calls[1] = the query itself
    assert embeddings.calls[0] == ["customer lookup", "order lookup"]

    # "restart": fresh tool closure, same persistent cache
    embeddings.calls.clear()
    second = create_discover(_deps(registry, embeddings, cache))
    assert json.loads(second("customer"))["id"] == "a"
    assert embeddings.calls == [["customer"]]  # query only — nothing re-embedded


def test_edited_description_is_re_embedded_only(tmp_path):
    cache = SqliteEmbeddingCache(str(tmp_path / "emb.db"))
    embeddings = FakeEmbeddings()
    create_discover(_deps(FakeRegistry({"a": "customer lookup", "b": "order lookup"}),
                          embeddings, cache))("customer")

    embeddings.calls.clear()
    edited = FakeRegistry({"a": "customer lookup v2", "b": "order lookup"})
    create_discover(_deps(edited, embeddings, cache))("customer")
    # only the delta re-embedded, then the query
    assert embeddings.calls == [["customer lookup v2"], ["customer"]]


def test_model_switch_re_embeds_everything(tmp_path):
    cache = SqliteEmbeddingCache(str(tmp_path / "emb.db"))
    embeddings = FakeEmbeddings()
    registry = FakeRegistry({"a": "customer lookup"})
    create_discover(_deps(registry, embeddings, cache))("customer")

    embeddings.model_name = "other-model"
    embeddings.calls.clear()
    create_discover(_deps(registry, embeddings, cache))("customer")
    assert embeddings.calls == [["customer lookup"], ["customer"]]


def test_cache_failure_falls_back_to_plain_embedding(tmp_path):
    class BrokenCache:
        def load(self, model):
            raise RuntimeError("disk gone")

        def save(self, embeddings):
            raise RuntimeError("disk gone")

        def ping(self):
            raise RuntimeError("disk gone")

    embeddings = FakeEmbeddings()
    discover = create_discover(
        _deps(FakeRegistry({"a": "customer lookup"}), embeddings, BrokenCache()))
    assert json.loads(discover("customer"))["id"] == "a"
    assert embeddings.calls == [["customer lookup"], ["customer"]]


def test_no_cache_behaves_as_before():
    embeddings = FakeEmbeddings()
    discover = create_discover(
        _deps(FakeRegistry({"a": "customer lookup"}), embeddings, None))
    assert json.loads(discover("customer"))["id"] == "a"
    assert embeddings.calls == [["customer lookup"], ["customer"]]


# --- m-16 embedding-cache edge cases (2026-10-25) ---

class _ShortEmbeddings(FakeEmbeddings):
    def embed(self, texts):
        self.calls.append(list(texts))
        # Returns fewer vectors than texts (a broken gateway).
        return [[1.0, 0.0]]


def test_short_embed_result_is_unavailable_not_key_error(tmp_path):
    cache = SqliteEmbeddingCache(str(tmp_path / "emb.db"))
    embeddings = _ShortEmbeddings()
    registry = FakeRegistry({"a": "customer lookup", "b": "order lookup"})
    tool = create_discover(_deps(registry, embeddings, cache))
    out = tool("customer")
    assert "temporarily unavailable" in out
    assert "cannot look it up" in out


def test_mixed_dimension_cache_is_re_embedded(tmp_path):
    cache = SqliteEmbeddingCache(str(tmp_path / "emb.db"))
    # Seed rows whose vectors disagree in dimension (model changed
    # under the same model string): 3-dim for "a", 2-dim for "b".
    from agent_api.ports.embedding_cache import CachedEmbedding

    cache.save([
        CachedEmbedding("a", __import__("hashlib").sha256(b"customer lookup").hexdigest(),
                        "test-model", (1.0, 0.0, 0.0)),
        CachedEmbedding("b", __import__("hashlib").sha256(b"order lookup").hexdigest(),
                        "test-model", (0.0, 1.0)),
    ])
    embeddings = FakeEmbeddings()
    registry = FakeRegistry({"a": "customer lookup", "b": "order lookup"})
    tool = create_discover(_deps(registry, embeddings, cache))
    # The mixed-dimension index must be discarded and re-embedded
    # (2-dim vectors from the fake), not ranked by truncation.
    assert json.loads(tool("customer"))["id"] == "a"
    assert any(len(call) == 2 for call in embeddings.calls)
