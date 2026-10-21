"""In-memory ConversationRepository (<Technology><PortName>, §2).

Process-local; fine for learning and tests. Swap for a database adapter later
without touching the service — that is the point of the port.
"""

from __future__ import annotations

from agent_api.domain.conversation import Conversation


class InMemoryConversationRepository:
    """ConversationRepository kept in a plain dict."""

    def __init__(self) -> None:
        self._store: dict[str, Conversation] = {}

    def load(self, conversation_id: str) -> Conversation:
        return self._store.setdefault(conversation_id, Conversation(id=conversation_id))

    def save(self, conversation: Conversation) -> None:
        self._store[conversation.id] = conversation
