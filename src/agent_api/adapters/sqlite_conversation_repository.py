"""SQLite-backed ConversationRepository (<Technology><PortName>, §2).

Durable across restarts, single host. For multi-instance deployments use
PostgresConversationRepository behind the same port — the service never
changes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path

from agent_api.domain.conversation import Conversation, ToolResult, Turn
from agent_api.ports.conversation_repository import (
    ConversationRepositoryError,
    SessionSummary,
)

logger = logging.getLogger(__name__)

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
        # closing(): sqlite3's `with conn` commits/rolls back but does
        # NOT close — the old code relied on refcounting (nit). Every
        # method here now closes explicitly and commits explicitly.
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    @staticmethod
    def _dump(conversation: Conversation) -> str:
        # Envelope form (ADR 0008): the owner travels with the turns in
        # the same turns_json column — no schema change. Rows written
        # before ownership existed hold a bare turns list; _load reads
        # those as owner None (see below).
        return json.dumps(
            {
                "owner": conversation.owner,
                "turns": [
                    {
                        "role": t.role,
                        "content": t.content,
                        "tool_uses": list(t.tool_uses),
                        "tool_results": [
                            {"tool": r.tool, "result": r.result}
                            for r in t.tool_results
                        ],
                    }
                    for t in conversation.turns
                ],
            }
        )

    @staticmethod
    def _load(conversation_id: str, turns_json: str | None) -> Conversation:
        # m-17: one corrupt turns_json row used to raise on every load
        # for that session — a permanent brick with no reset path short
        # of DB surgery. A malformed row now logs and yields an empty
        # conversation (the session is recoverable; the next save
        # overwrites the corrupt row). A reset endpoint also exists
        # (DELETE /api/sessions/{id}) for callers that want a fresh start.
        if not turns_json:
            return Conversation(id=conversation_id, turns=[])
        try:
            document = json.loads(turns_json)
            # Legacy rows (pre-ownership) hold a bare turns list: they
            # deserialize with owner None and are claimable once by the
            # first authenticated writer, then bound.
            if isinstance(document, list):
                items, owner = document, None
            elif isinstance(document, dict):
                items, owner = document.get("turns"), document.get("owner")
                if owner is not None and not isinstance(owner, str):
                    raise ValueError("owner is not a string")
            else:
                raise ValueError("turns_json is neither a list nor an envelope")
            if not isinstance(items, list):
                raise ValueError("turns_json is not a list")
            turns = []
            for item in items:
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
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.warning(
                "sqlite store: corrupt history for session %s (%s); "
                "returning an empty conversation",
                conversation_id, exc,
            )
            return Conversation(id=conversation_id, turns=[])
        return Conversation(id=conversation_id, turns=turns, owner=owner)

    def load(self, conversation_id: str) -> Conversation:
        try:
            with closing(self._connect()) as conn:
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
            with closing(self._connect()) as conn:
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
            with closing(self._connect()) as conn:
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

    @staticmethod
    def _owner_of(turns_json: str | None) -> str | None:
        """The owner bound in a row, or None for legacy/unowned rows.

        Only the envelope's owner is read — the turns are never parsed
        for a listing. A corrupt row yields None (it lists in dev mode
        and is invisible to authenticated callers, like any unowned
        row); load() is what logs and recovers it.
        """
        if not turns_json:
            return None
        try:
            document = json.loads(turns_json)
        except ValueError:
            return None
        if isinstance(document, dict):
            owner = document.get("owner")
            return owner if isinstance(owner, str) else None
        return None

    def list_sessions(self, owner: str | None) -> list[SessionSummary]:
        # The table carries no updated_at column, so summaries carry
        # None there (the port documents that). Ownership is filtered
        # in Python rather than via json_extract(): the JSON1
        # functions are not guaranteed in every bundled SQLite, and
        # the row count here is bounded by MAX_SESSIONS-scale usage.
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    "SELECT id, turns_json FROM conversations"
                ).fetchall()
            summaries: list[SessionSummary] = []
            for row_id, turns_json in rows:
                if row_id == _PROBE_ID:
                    continue
                row_owner = self._owner_of(turns_json)
                if owner is None or row_owner == owner:
                    summaries.append(
                        SessionSummary(id=row_id, owner=row_owner)
                    )
            return summaries
        except ConversationRepositoryError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConversationRepositoryError(
                f"sqlite store list failed: {exc}"
            ) from exc
