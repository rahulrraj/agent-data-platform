"""Outbound port: the core's contract for conversation history.

History is data this application owns, so this is a repository port (§4).
"""

from __future__ import annotations

from typing import Protocol

from agent_api.domain.conversation import Conversation


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
