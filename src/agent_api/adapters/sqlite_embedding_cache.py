"""SQLite-backed EmbeddingCache (<Technology><PortName>, §2).

One row per (capability id, embedding model). Vectors are stored as
float64 blobs; a few thousand 768-dim vectors is on the order of 10 MB.
"""

from __future__ import annotations

import array
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from agent_api.ports.embedding_cache import CachedEmbedding, EmbeddingCacheError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_embeddings (
    capability_id TEXT NOT NULL,
    model TEXT NOT NULL,
    description_hash TEXT NOT NULL,
    vector BLOB NOT NULL,
    vector_dim INTEGER,
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
        try:
            # closing(): sqlite3's `with conn` commits/rolls back but
            # does NOT close the connection — the old code relied on
            # refcounting to close it (nit). Every method here now
            # closes explicitly.
            with closing(sqlite3.connect(path)) as conn:
                conn.execute(_SCHEMA)
                # Migration for pre-vector_dim databases: CREATE TABLE
                # IF NOT EXISTS never adds the column to an old file.
                columns = {
                    row[1]
                    for row in conn.execute(
                        "PRAGMA table_info(capability_embeddings)"
                    ).fetchall()
                }
                if "vector_dim" not in columns:
                    conn.execute(
                        "ALTER TABLE capability_embeddings "
                        "ADD COLUMN vector_dim INTEGER"
                    )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingCacheError(f"embedding cache init failed: {exc}") from exc

    def load(self, model: str) -> dict[str, CachedEmbedding]:
        try:
            with closing(sqlite3.connect(self._path)) as conn:
                rows = conn.execute(
                    "SELECT capability_id, description_hash, vector, vector_dim"
                    " FROM capability_embeddings WHERE model = ?",
                    (model,),
                ).fetchall()
            return {
                capability_id: CachedEmbedding(
                    capability_id, description_hash, model,
                    _decode(vector), vector_dim,
                )
                for capability_id, description_hash, vector, vector_dim in rows
            }
        except EmbeddingCacheError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingCacheError(f"embedding cache load failed: {exc}") from exc

    def save(self, embeddings: list[CachedEmbedding]) -> None:
        try:
            now = datetime.now(timezone.utc).isoformat()
            with closing(sqlite3.connect(self._path)) as conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO capability_embeddings"
                    " (capability_id, model, description_hash, vector,"
                    "  vector_dim, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (e.capability_id, e.model, e.description_hash,
                         _encode(e.vector), len(e.vector), now)
                        for e in embeddings
                    ],
                )
                conn.commit()
        except EmbeddingCacheError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingCacheError(f"embedding cache save failed: {exc}") from exc

    def prune_models(self, keep: set[str]) -> None:
        """Delete rows for models no longer in use (m-16c).

        Switching embedding models used to leave old-model rows in the
        file forever. The caller passes the one model in use; anything
        else is stale. Best-effort from discovery's side.
        """
        try:
            with closing(sqlite3.connect(self._path)) as conn:
                if keep:
                    placeholders = ",".join("?" for _ in keep)
                    conn.execute(
                        "DELETE FROM capability_embeddings "
                        f"WHERE model NOT IN ({placeholders})",
                        tuple(keep),
                    )
                else:
                    conn.execute("DELETE FROM capability_embeddings")
                conn.commit()
        except EmbeddingCacheError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingCacheError(f"embedding cache prune failed: {exc}") from exc

    def ping(self) -> None:
        try:
            with closing(sqlite3.connect(self._path)) as conn:
                conn.execute("SELECT 1 FROM capability_embeddings LIMIT 1").fetchone()
        except EmbeddingCacheError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingCacheError(f"embedding cache ping failed: {exc}") from exc
