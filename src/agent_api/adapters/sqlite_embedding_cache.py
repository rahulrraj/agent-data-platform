"""SQLite-backed EmbeddingCache (<Technology><PortName>, §2).

One row per (capability id, embedding model). Vectors are stored as
float64 blobs; a few thousand 768-dim vectors is on the order of 10 MB.
"""

from __future__ import annotations

import array
import sqlite3
from datetime import datetime, timezone

from agent_api.ports.embedding_cache import CachedEmbedding

_SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_embeddings (
    capability_id TEXT NOT NULL,
    model TEXT NOT NULL,
    description_hash TEXT NOT NULL,
    vector BLOB NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (capability_id, model)
)
"""


def _encode(vector) -> bytes:
    return array.array("d", vector).tobytes()


def _decode(blob: bytes) -> tuple[float, ...]:
    arr = array.array("d")
    arr.frombytes(bytes(blob))
    return tuple(arr)


class SqliteEmbeddingCache:
    """EmbeddingCache persisted in a SQLite file."""

    def __init__(self, path: str) -> None:
        self._path = path
        with sqlite3.connect(path) as conn:
            conn.execute(_SCHEMA)

    def load(self, model: str) -> dict[str, CachedEmbedding]:
        with sqlite3.connect(self._path) as conn:
            rows = conn.execute(
                "SELECT capability_id, description_hash, vector"
                " FROM capability_embeddings WHERE model = ?",
                (model,),
            ).fetchall()
        return {
            capability_id: CachedEmbedding(capability_id, description_hash, model, _decode(vector))
            for capability_id, description_hash, vector in rows
        }

    def save(self, embeddings: list[CachedEmbedding]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self._path) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO capability_embeddings"
                " (capability_id, model, description_hash, vector, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    (e.capability_id, e.model, e.description_hash, _encode(e.vector), now)
                    for e in embeddings
                ],
            )

    def ping(self) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.execute("SELECT 1 FROM capability_embeddings LIMIT 1").fetchone()
