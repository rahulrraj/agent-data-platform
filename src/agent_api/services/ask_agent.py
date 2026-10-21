"""Use case: ask the agent.

Owns the application orchestration for a chat turn: load history, run the
model, record the reply. Depends only on ports and domain — never on concrete
adapters or the HTTP layer (§3). Port errors become application errors (§5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

from agent_api.domain.conversation import Conversation
from agent_api.ports.conversation_repository import ConversationRepository
from agent_api.ports.model_gateway import ModelGateway, ModelGatewayError, StreamChunk


class AskAgentError(Exception):
    """Base for application-level failures of this use case."""


class InvalidMessageError(AskAgentError):
    """The caller's message failed validation."""


class AgentServiceError(AskAgentError):
    """The model call failed after translation from the port error."""


@dataclass(frozen=True)
class AgentReply:
    text: str
    tool_uses: tuple[str, ...]


class AskAgentService:
    """The 'ask the agent' use case."""

    def __init__(self, gateway: ModelGateway, conversations: ConversationRepository) -> None:
        self._gateway = gateway
        self._conversations = conversations

    def _validated(self, conversation_id: str, message: str) -> Conversation:
        if not message.strip():
            raise InvalidMessageError("message is required")
        conversation = self._conversations.load(conversation_id)
        conversation.add_user(message)
        return conversation

    def ask(self, conversation_id: str, message: str) -> AgentReply:
        """One full agentic turn; returns the reply and the tools it used."""
        conversation = self._validated(conversation_id, message)
        try:
            turn = self._gateway.generate(conversation)
        except ModelGatewayError as exc:
            raise AgentServiceError(str(exc)) from exc
        conversation.add_assistant(turn.content, turn.tool_uses)
        self._conversations.save(conversation)
        return AgentReply(text=turn.content, tool_uses=turn.tool_uses)

    async def ask_stream(
        self, conversation_id: str, message: str
    ) -> AsyncIterator[StreamChunk]:
        """Same as ask(), but yields text/tool chunks as they arrive."""
        conversation = self._validated(conversation_id, message)
        texts: list[str] = []
        tools: list[str] = []
        try:
            async for chunk in self._gateway.stream(conversation):
                if chunk.kind == "text":
                    texts.append(chunk.payload)
                elif chunk.kind == "tool" and chunk.payload not in tools:
                    tools.append(chunk.payload)
                yield chunk
        except ModelGatewayError as exc:
            raise AgentServiceError(str(exc)) from exc
        conversation.add_assistant("".join(texts), tools)
        self._conversations.save(conversation)
