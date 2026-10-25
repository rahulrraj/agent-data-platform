"""Postgres-backed ConversationRepository (<Technology><PortName>, §2).

Durable and shared across instances: every replica reads and writes the
same table, so any instance can serve any session. Requires the
`postgres` extra: `pip install -e ".[postgres]"` (psycopg is imported
lazily so the core stays dependency-light).
"""

from __future__ import annotations

import json
import logging

from agent_api.domain.conversation import Conversation, ToolResult, Turn
from agent_api.ports.conversation_repository import ConversationRepositoryError

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    turns_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _psycopg():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            'PostgresConversationRepository needs the "postgres" extra: '
            'pip install -e ".[postgres]"'
        ) from exc
    return psycopg


class PostgresConversationRepository:
    """ConversationRepository persisted in Postgres."""

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("POSTGRES_DSN is not set")
        self._dsn = dsn
        try:
            psycopg = _psycopg()
            with psycopg.connect(dsn) as conn:
                conn.execute(_SCHEMA)
                conn.commit()
        except ConversationRepositoryError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConversationRepositoryError(f"postgres store init failed: {exc}") from exc

    def _connect(self):
        return _psycopg().connect(self._dsn)

    @staticmethod
    def _dump(conversation: Conversation) -> str:
        return json.dumps(
            [
                {
                    "role": t.role,
                    "content": t.content,
                    "tool_uses": list(t.tool_uses),
                    "tool_results": [
                        {"tool": r.tool, "result": r.result} for r in t.tool_results
                    ],
                }
                for t in conversation.turns
            ]
        )

    @staticmethod
    def _load(conversation_id: str, turns) -> Conversation:
        # m-17: same corrupt-row fallback as the SQLite adapter — a
        # malformed row logs and yields an empty conversation instead
        # of bricking the session on every load. (psycopg's `with conn`
        # already closes the connection, so no closing change is needed
        # here, unlike the sqlite3 adapters.)
        if turns is None:
            return Conversation(id=conversation_id, turns=[])
        try:
            items = turns if isinstance(turns, list) else json.loads(turns or "[]")
            if not isinstance(items, list):
                raise ValueError("turns_json is not a list")
            parsed = [
                Turn(
                    role=item["role"],
                    content=item["content"],
                    tool_uses=tuple(item.get("tool_uses", [])),
                    tool_results=tuple(
                        ToolResult(tool=r["tool"], result=r["result"])
                        for r in item.get("tool_results", [])
                    ),
                )
                for item in items
            ]
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.warning(
                "postgres store: corrupt history for session %s (%s); "
                "returning an empty conversation",
                conversation_id, exc,
            )
            return Conversation(id=conversation_id, turns=[])
        return Conversation(id=conversation_id, turns=parsed)

    def load(self, conversation_id: str) -> Conversation:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT turns_json FROM conversations WHERE id = %s",
                    (conversation_id,),
                ).fetchone()
            return self._load(conversation_id, row[0] if row else None)
        except ConversationRepositoryError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConversationRepositoryError(f"postgres store load failed: {exc}") from exc

    def save(self, conversation: Conversation) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO conversations (id, turns_json) VALUES (%s, %s::jsonb) "
                    "ON CONFLICT (id) DO UPDATE SET "
                    "turns_json = EXCLUDED.turns_json, updated_at = now()",
                    (conversation.id, self._dump(conversation)),
                )
                conn.commit()
        except ConversationRepositoryError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConversationRepositoryError(f"postgres store save failed: {exc}") from exc

    def ping(self) -> None:
        """Writability probe: write a probe row, then delete it again."""
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO conversations (id, turns_json) VALUES (%s, %s::jsonb) "
                    "ON CONFLICT (id) DO UPDATE SET turns_json = EXCLUDED.turns_json",
                    ("__readiness_probe__", "[]"),
                )
                conn.execute(
                    "DELETE FROM conversations WHERE id = %s", ("__readiness_probe__",)
                )
                conn.commit()
        except ConversationRepositoryError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConversationRepositoryError(f"postgres store ping failed: {exc}") from exc
