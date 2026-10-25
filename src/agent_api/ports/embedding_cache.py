"""EmbeddingCache: persist capability description vectors across restarts.

At thousands of capabilities, re-embedding every description on each
process start is a slow cold start and real API cost. This port stores
vectors keyed by (capability id, embedding model) with a hash of the
description text, so only new or edited descriptions are re-embedded.
Switching embedding models invalidates everything, which is correct:
vectors from different models are not comparable.

The cache is best-effort: discovery works without it, just slower on a
cold start. Adapter: SQLite (sqlite_embedding_cache).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CachedEmbedding:
    capability_id: str
    description_hash: str  # sha256 of the description text that was embedded
    model: str  # embedding model id, e.g. "nomic-embed-text"
    vector: tuple[float, ...]


class EmbeddingCacheError(Exception):
    """An embedding-cache operation failed (load/save/ping)."""


class EmbeddingCache(Protocol):
    """Outbound port for persisted description vectors."""

    def load(self, model: str) -> dict[str, CachedEmbedding]:
        """All cached vectors for this embedding model, keyed by capability id."""
        ...

    def save(self, embeddings: list[CachedEmbedding]) -> None:
        """Upsert vectors."""
        ...

    def ping(self) -> None:
        """Raise when the cache is unreadable or unwritable."""
        ...
