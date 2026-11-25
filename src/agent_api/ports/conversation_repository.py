"""Outbound port: the core's contract for conversation history.

History is data this application owns, so this is a repository port (§4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from agent_api.domain.conversation import Conversation


class ConversationRepositoryError(Exception):
    """A conversation store operation failed (load/save/ping)."""


@dataclass(frozen=True)
class SessionSummary:
    """One row of a per-user session listing: id + recency, no content.

    `owner` is the principal the session is bound to (None for
    unowned/legacy rows). `updated_at` is the store's last-write time
    where the store tracks one (Postgres); None where it does not
    (memory/SQLite have no such column).
    """

    id: str
    owner: str | None = None
    updated_at: datetime | None = None


class ConversationRepository(Protocol):
    """Load/save the application's own conversation records."""

    def load(self, conversation_id: str) -> Conversation:
        """Return the conversation, or a new empty one if none exists yet."""
        ...

    def save(self, conversation: Conversation) -> None:
        """Persist the conversation."""
        ...

    def ping(self) -> None:
        """Cheap writability check; raise when the store is unavailable."""
        ...

    def list_sessions(self, owner: str | None) -> list[SessionSummary]:
        """List session summaries visible to `owner`.

        `owner` None means dev mode (auth off): every session lists,
        including unowned legacy rows. An authenticated caller sees
        only sessions bound to that principal — never another
        principal's ids, and never unowned rows (an unowned row is
        unclaimed: it becomes visible to its claimer only once a
        turn binds it, via _enforce_owner).
        """
        ...
