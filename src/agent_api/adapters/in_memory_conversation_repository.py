"""In-memory ConversationRepository (<Technology><PortName>, §2).

Process-local; fine for learning and tests. Swap for a database adapter later
without touching the service — that is the point of the port.
"""

from __future__ import annotations

from agent_api.domain.conversation import Conversation
from agent_api.ports.conversation_repository import ConversationRepositoryError


class InMemoryConversationRepository:
    """ConversationRepository kept in a plain dict."""

    def __init__(self) -> None:
        self._store: dict[str, Conversation] = {}

    def load(self, conversation_id: str) -> Conversation:
        try:
            return self._store.setdefault(conversation_id, Conversation(id=conversation_id))
        except ConversationRepositoryError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConversationRepositoryError(f"memory store load failed: {exc}") from exc

    def save(self, conversation: Conversation) -> None:
        try:
            self._store[conversation.id] = conversation
        except ConversationRepositoryError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConversationRepositoryError(f"memory store save failed: {exc}") from exc

    def ping(self) -> None:
        # Process-local dict: nothing to check.
        return None
