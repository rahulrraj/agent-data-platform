"""Tests for the SQLite embedding cache."""

from __future__ import annotations

import pytest

from agent_api.adapters.sqlite_embedding_cache import SqliteEmbeddingCache
from agent_api.ports.embedding_cache import CachedEmbedding


def _row(cid, dh="hash1", model="m", vector=(0.1, 0.2, 0.3)):
    return CachedEmbedding(cid, dh, model, vector)


def test_round_trip(tmp_path):
    cache = SqliteEmbeddingCache(str(tmp_path / "emb.db"))
    cache.save([_row("a"), _row("b", vector=(1.0, 2.0))])
    loaded = cache.load("m")
    assert set(loaded) == {"a", "b"}
    assert loaded["a"].description_hash == "hash1"
    assert loaded["b"].vector == (1.0, 2.0)
    assert loaded["a"].model == "m"


def test_load_is_scoped_to_model(tmp_path):
    cache = SqliteEmbeddingCache(str(tmp_path / "emb.db"))
    cache.save([_row("a", model="m1")])
    assert cache.load("m2") == {}
    assert set(cache.load("m1")) == {"a"}


def test_save_upserts(tmp_path):
    cache = SqliteEmbeddingCache(str(tmp_path / "emb.db"))
    cache.save([_row("a", dh="old", vector=(0.0,))])
    cache.save([_row("a", dh="new", vector=(9.0,))])
    loaded = cache.load("m")
    assert loaded["a"].description_hash == "new"
    assert loaded["a"].vector == (9.0,)


def test_ping_ok(tmp_path):
    SqliteEmbeddingCache(str(tmp_path / "emb.db")).ping()


def test_ping_bad_path():
    with pytest.raises(Exception):
        SqliteEmbeddingCache("/nonexistent-dir-xyz/emb.db").ping()


def test_vector_dim_recorded_and_prune_drops_other_models(tmp_path):
    cache = SqliteEmbeddingCache(str(tmp_path / "emb.db"))
    cache.save([_row("a", model="m1"), _row("b", model="m2")])
    assert cache.load("m1")["a"].vector_dim == 3
    cache.prune_models({"m1"})
    assert set(cache.load("m1")) == {"a"}
    assert cache.load("m2") == {}
