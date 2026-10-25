"""Use case: ask the agent.

Owns the application orchestration for a chat turn: load history, run the
model, record the reply. Depends only on ports and domain — never on concrete
adapters or the HTTP layer (§3). Port errors become application errors (§5).

History consistency rules (the service, not any one backend, owns them,
so memory/sqlite/postgres behave identically):

- The user turn is saved *before* the gateway runs, so a failed turn
  leaves the same history on every backend (previously the memory backend
  kept the failed user turn and the SQL backends lost it).
- A failed or aborted stream still persists what arrived, marked
  "[partial response — stream interrupted]", instead of losing both
  messages.
- Concurrent turns on one session are serialized by a per-session lock,
  so two turns cannot load-append-save over each other (lost update).
  The lock is polled non-blockingly in the streaming path so waiting
  never blocks the event loop.
- The gateway never sees more than MAX_HISTORY_TURNS turns: history is
  truncated to the last N turns at handoff (the stored conversation is
  kept whole — truncation is a handoff window, not deletion), so an
  over-long session cannot brick itself against the model context window.
  `reset_session()` clears a session outright when a caller wants a
  fresh start.
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from typing import AsyncIterator

from agent_api.domain.conversation import Conversation, ToolResult
from agent_api.ports.capability_repository import (
    CapabilityRepository,
    CapabilityRepositoryError,
)
from agent_api.ports.conversation_repository import (
    ConversationRepository,
    ConversationRepositoryError,
)
from agent_api.ports.embedding_gateway import EmbeddingGateway, EmbeddingGatewayError
from agent_api.ports.model_gateway import ModelGateway, ModelGatewayError, StreamChunk

# History window handed to the gateway: 40 turns = 20 exchanges. Large
# enough for real follow-ups, small enough to stay inside small-model
# context windows. Stored history is NOT truncated — see module docstring.
MAX_HISTORY_TURNS = 40

PARTIAL_MARKER = "[partial response — stream interrupted]"


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


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    failing: tuple[str, ...] = ()


class AskAgentService:
    """The 'ask the agent' use case."""

    def __init__(
        self,
        gateway: ModelGateway,
        conversations: ConversationRepository,
        *,
        embedding_gateway: EmbeddingGateway | None = None,
        capability_registry: CapabilityRepository | None = None,
    ) -> None:
        self._gateway = gateway
        self._conversations = conversations
        self._embedding_gateway = embedding_gateway
        self._capability_registry = capability_registry
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- session locking ---------------------------------------------------

    def _session_lock(self, conversation_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(conversation_id, threading.Lock())

    # -- history helpers ---------------------------------------------------

    def _validated(self, conversation_id: str, message: str) -> Conversation:
        if not message.strip():
            raise InvalidMessageError("message is required")
        try:
            conversation = self._conversations.load(conversation_id)
        except ConversationRepositoryError as exc:
            raise AgentServiceError(str(exc)) from exc
        conversation.add_user(message)
        return conversation

    def _save(self, conversation: Conversation) -> None:
        try:
            self._conversations.save(conversation)
        except ConversationRepositoryError as exc:
            raise AgentServiceError(str(exc)) from exc

    @staticmethod
    def _gateway_conversation(conversation: Conversation) -> Conversation:
        """The handoff window: last MAX_HISTORY_TURNS turns, as a copy.

        A copy, so gateway seeding can never mutate the stored history.
        """
        return Conversation(
            id=conversation.id, turns=list(conversation.turns[-MAX_HISTORY_TURNS:])
        )

    def reset_session(self, conversation_id: str) -> None:
        """Clear a session's history (fresh start for a bricked session)."""
        with self._session_lock(conversation_id):
            self._save(Conversation(id=conversation_id))

    # -- turns --------------------------------------------------------------

    def ask(self, conversation_id: str, message: str) -> AgentReply:
        """One full agentic turn; returns the reply and the tools it used."""
        with self._session_lock(conversation_id):
            conversation = self._validated(conversation_id, message)
            # Persist the user turn before the gateway runs (see module
            # docstring): a gateway failure must not diverge backends.
            self._save(conversation)
            try:
                turn = self._gateway.generate(self._gateway_conversation(conversation))
            except ModelGatewayError as exc:
                raise AgentServiceError(str(exc)) from exc
            conversation.add_assistant(
                turn.content, turn.tool_uses, turn.tool_results
            )
            self._save(conversation)
            return AgentReply(text=turn.content, tool_uses=turn.tool_uses)

    async def ask_stream(
        self, conversation_id: str, message: str
    ) -> AsyncIterator[StreamChunk]:
        """Same as ask(), but yields text/tool chunks as they arrive."""
        lock = self._session_lock(conversation_id)
        conversation: Conversation | None = None
        texts: list[str] = []
        tools: list[str] = []
        tool_results: list[ToolResult] = []
        saved = False
        assistant_appended = False
        try:
            # Poll a non-blocking acquire: a blocking acquire here would
            # stall the event loop while another turn holds the session.
            while not lock.acquire(blocking=False):
                await asyncio.sleep(0.05)
            try:
                conversation = self._validated(conversation_id, message)
                self._save(conversation)  # user turn, before the gateway
                try:
                    async for chunk in self._gateway.stream(
                        self._gateway_conversation(conversation)
                    ):
                        if chunk.kind == "text":
                            texts.append(chunk.payload)
                        elif chunk.kind == "tool" and chunk.payload not in tools:
                            tools.append(chunk.payload)
                        elif chunk.kind == "tool_result":
                            try:
                                data = json.loads(chunk.payload)
                                tool_results.append(
                                    ToolResult(
                                        tool=data["tool"], result=data["result"]
                                    )
                                )
                            except (ValueError, KeyError, TypeError):
                                pass
                        yield chunk
                except ModelGatewayError as exc:
                    # Persist the partial answer with its marker, then
                    # report the failure (partial save is best-effort:
                    # the gateway error is the one the caller must see).
                    if conversation is not None and (texts or tools or tool_results):
                        self._persist_partial(conversation, texts, tools, tool_results)
                        saved = True
                    raise AgentServiceError(str(exc)) from exc
                conversation.add_assistant("".join(texts), tools, tool_results)
                assistant_appended = True
                self._save(conversation)
                saved = True
            finally:
                lock.release()
        finally:
            # Abort path (generator closed / cancelled before completion):
            # except-clauses above never run for GeneratorExit, so the
            # partial answer is persisted here instead of being lost.
            # (Skipped when the assistant turn was already appended — a
            # failed final save must not duplicate it as a partial turn.)
            if (
                not saved
                and not assistant_appended
                and conversation is not None
                and (texts or tools or tool_results)
            ):
                try:
                    self._persist_partial(conversation, texts, tools, tool_results)
                except Exception:  # noqa: BLE001 — never raise from cleanup
                    pass

    @staticmethod
    def _partial_text(texts: list[str]) -> str:
        body = "".join(texts)
        return f"{body}\n\n{PARTIAL_MARKER}" if body else PARTIAL_MARKER

    def _persist_partial(
        self,
        conversation: Conversation,
        texts: list[str],
        tools: list[str],
        tool_results: list[ToolResult],
    ) -> None:
        conversation.add_assistant(
            self._partial_text(texts), tools, tool_results
        )
        try:
            self._conversations.save(conversation)
        except ConversationRepositoryError:
            pass

    # -- readiness ----------------------------------------------------------

    def readiness(self) -> ReadinessReport:
        """Check that dependencies are reachable. For load-balancer readiness.

        Honest limits, documented where they live: Gemini/Bedrock model
        and embedding pings are config-only (key/credentials present) —
        a live probe would burn quota/tokens on every check, so the first
        real call is what proves inference. Ollama pings check the tag
        list *and* model presence. The store ping is a write-then-delete
        probe row, not a bare SELECT 1. Embedding and registry probes
        run only when those dependencies are wired (they always are in
        the composed app; bare service constructions skip them).
        """
        failing: list[str] = []
        try:
            self._gateway.ping()
        except ModelGatewayError as exc:
            failing.append(f"model: {exc}")
        try:
            self._conversations.ping()
        except ConversationRepositoryError as exc:
            failing.append(f"store: {exc}")
        if self._embedding_gateway is not None:
            try:
                self._embedding_gateway.ping()
            except EmbeddingGatewayError as exc:
                failing.append(f"embedding: {exc}")
        if self._capability_registry is not None:
            try:
                self._capability_registry.ping()
            except CapabilityRepositoryError as exc:
                failing.append(f"registry: {exc}")
        return ReadinessReport(ready=not failing, failing=tuple(failing))
