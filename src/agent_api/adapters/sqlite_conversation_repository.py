"""SQLite-backed ConversationRepository (<Technology><PortName>, §2).

Durable across restarts, single host. For true multi-instance deployments,
add a Postgres adapter behind the same port — the service never changes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agent_api.domain.conversation import Conversation, Turn

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    turns_json TEXT NOT NULL
)
"""


class SqliteConversationRepository:
    """ConversationRepository persisted in a SQLite file."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        parent = Path(self._path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @staticmethod
    def _dump(conversation: Conversation) -> str:
        return json.dumps(
            [
                {"role": t.role, "content": t.content, "tool_uses": list(t.tool_uses)}
                for t in conversation.turns
            ]
        )

    @staticmethod
    def _load(conversation_id: str, turns_json: str | None) -> Conversation:
        turns = []
        if turns_json:
            for item in json.loads(turns_json):
                turns.append(
                    Turn(
                        role=item["role"],
                        content=item["content"],
                        tool_uses=tuple(item.get("tool_uses", [])),
                    )
                )
        return Conversation(id=conversation_id, turns=turns)

    def load(self, conversation_id: str) -> Conversation:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT turns_json FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        return self._load(conversation_id, row[0] if row else None)

    def save(self, conversation: Conversation) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations (id, turns_json) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET turns_json = excluded.turns_json",
                (conversation.id, self._dump(conversation)),
            )
            conn.commit()

    def ping(self) -> None:
        with self._connect() as conn:
            conn.execute("SELECT 1").fetchone()
