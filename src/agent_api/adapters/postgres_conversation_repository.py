"""Postgres-backed ConversationRepository (<Technology><PortName>, §2).

Durable and shared across instances: every replica reads and writes the
same table, so any instance can serve any session. Requires the
`postgres` extra: `pip install -e ".[postgres]"` (psycopg is imported
lazily so the core stays dependency-light).
"""

from __future__ import annotations

import json

from agent_api.domain.conversation import Conversation, Turn

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
        psycopg = _psycopg()
        with psycopg.connect(dsn) as conn:
            conn.execute(_SCHEMA)
            conn.commit()

    def _connect(self):
        return _psycopg().connect(self._dsn)

    @staticmethod
    def _dump(conversation: Conversation) -> str:
        return json.dumps(
            [
                {"role": t.role, "content": t.content, "tool_uses": list(t.tool_uses)}
                for t in conversation.turns
            ]
        )

    @staticmethod
    def _load(conversation_id: str, turns) -> Conversation:
        items = turns if isinstance(turns, list) else json.loads(turns or "[]")
        return Conversation(
            id=conversation_id,
            turns=[
                Turn(
                    role=item["role"],
                    content=item["content"],
                    tool_uses=tuple(item.get("tool_uses", [])),
                )
                for item in items
            ],
        )

    def load(self, conversation_id: str) -> Conversation:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT turns_json FROM conversations WHERE id = %s",
                (conversation_id,),
            ).fetchone()
        return self._load(conversation_id, row[0] if row else None)

    def save(self, conversation: Conversation) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations (id, turns_json) VALUES (%s, %s::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET "
                "turns_json = EXCLUDED.turns_json, updated_at = now()",
                (conversation.id, self._dump(conversation)),
            )
            conn.commit()

    def ping(self) -> None:
        with self._connect() as conn:
            conn.execute("SELECT 1").fetchone()
