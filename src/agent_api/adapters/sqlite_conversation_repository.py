"""SQLite-backed ConversationRepository (<Technology><PortName>, §2).

Durable across restarts, single host. For multi-instance deployments use
PostgresConversationRepository behind the same port — the service never
changes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agent_api.domain.conversation import Conversation, ToolResult, Turn
from agent_api.ports.conversation_repository import ConversationRepositoryError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    turns_json TEXT NOT NULL
)
"""

_PROBE_ID = "__readiness_probe__"


class SqliteConversationRepository:
    """ConversationRepository persisted in a SQLite file."""

    def __init__(self, path: str) -> None:
        self._path = path
        try:
            self._init_schema()
        except Exception as exc:  # noqa: BLE001
            raise ConversationRepositoryError(f"sqlite store init failed: {exc}") from exc

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
    def _load(conversation_id: str, turns_json: str | None) -> Conversation:
        turns = []
        if turns_json:
            for item in json.loads(turns_json):
                turns.append(
                    Turn(
                        role=item["role"],
                        content=item["content"],
                        tool_uses=tuple(item.get("tool_uses", [])),
                        tool_results=tuple(
                            ToolResult(tool=r["tool"], result=r["result"])
                            for r in item.get("tool_results", [])
                        ),
                    )
                )
        return Conversation(id=conversation_id, turns=turns)

    def load(self, conversation_id: str) -> Conversation:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT turns_json FROM conversations WHERE id = ?",
                    (conversation_id,),
                ).fetchone()
            return self._load(conversation_id, row[0] if row else None)
        except ConversationRepositoryError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConversationRepositoryError(f"sqlite store load failed: {exc}") from exc

    def save(self, conversation: Conversation) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO conversations (id, turns_json) VALUES (?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET turns_json = excluded.turns_json",
                    (conversation.id, self._dump(conversation)),
                )
                conn.commit()
        except ConversationRepositoryError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConversationRepositoryError(f"sqlite store save failed: {exc}") from exc

    def ping(self) -> None:
        """Writability probe: write a probe row, then delete it again.

        A bare SELECT 1 proves the file is readable, not that turns can be
        saved; readiness must prove the write path.
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO conversations (id, turns_json) VALUES (?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET turns_json = excluded.turns_json",
                    (_PROBE_ID, "[]"),
                )
                conn.execute(
                    "DELETE FROM conversations WHERE id = ?", (_PROBE_ID,)
                )
                conn.commit()
        except ConversationRepositoryError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConversationRepositoryError(f"sqlite store ping failed: {exc}") from exc
