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
from agent_api.platform.request_context import request_context
from agent_api.ports.capability_repository import (
    CapabilityRepository,
    CapabilityRepositoryError,
)
from agent_api.ports.conversation_repository import (
    ConversationRepository,
    ConversationRepositoryError,
)
from agent_api.ports.embedding_gateway import EmbeddingGateway, EmbeddingGatewayError
from agent_api.ports.model_gateway import (
    ModelGateway,
    ModelGatewayError,
    ModelGatewayResolver,
    StreamChunk,
)

# History window handed to the gateway: 40 turns = 20 exchanges. Large
# enough for real follow-ups, small enough to stay inside small-model
# context windows. Stored history is NOT truncated — see module docstring.
MAX_HISTORY_TURNS = 40

PARTIAL_MARKER = "[partial response — stream interrupted]"


class AskAgentError(Exception):
    """Base for application-level failures of this use case."""


class InvalidMessageError(AskAgentError):
    """The caller's message failed validation."""


class ForbiddenError(AskAgentError):
    """The session belongs to a different principal (ADR 0008 → 403)."""


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
    """The 'ask the agent' use case.

    The chat model is resolved per call: either the single injected
    gateway, or — when a gateway_resolver is wired (the composed app) —
    the gateway for the requested allowlist key (ADR 0007). The
    resolver owns the framework × provider matrix; this service only
    knows the resolver contract and the key the caller asked for.
    """

    def __init__(
        self,
        gateway: ModelGateway | None = None,
        conversations: ConversationRepository | None = None,
        *,
        embedding_gateway: EmbeddingGateway | None = None,
        capability_registry: CapabilityRepository | None = None,
        gateway_resolver: ModelGatewayResolver | None = None,
    ) -> None:
        if gateway is None and gateway_resolver is None:
            raise TypeError(
                "AskAgentService needs a gateway or a gateway_resolver"
            )
        if conversations is None:
            raise TypeError("AskAgentService needs a conversations repository")
        self._gateway = gateway
        self._gateway_resolver = gateway_resolver
        self._conversations = conversations
        self._embedding_gateway = embedding_gateway
        self._capability_registry = capability_registry
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # -- model resolution --------------------------------------------------

    def _resolve_gateway(self, model: str | None) -> ModelGateway:
        """The gateway for a requested model key.

        None/"default" is the configured model. An unknown key is an
        InvalidMessageError (400) listing the available keys — never a
        guess, and never a caller-supplied provider/model pair.
        """
        if self._gateway_resolver is not None:
            try:
                return self._gateway_resolver.gateway_for(model)
            except KeyError:
                keys = ", ".join(self._gateway_resolver.available_keys)
                raise InvalidMessageError(
                    f"unknown model {model!r}; available models: {keys}"
                ) from None
        if model not in (None, "default"):
            raise InvalidMessageError(
                f"unknown model {model!r}; available models: default"
            )
        assert self._gateway is not None  # guaranteed by __init__
        return self._gateway

    # -- session locking ---------------------------------------------------

    def _session_lock(self, conversation_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(conversation_id, threading.Lock())

    # -- history helpers ---------------------------------------------------

    def _validated(
        self, conversation_id: str, message: str, principal: str | None = None
    ) -> Conversation:
        if not message.strip():
            raise InvalidMessageError("message is required")
        try:
            conversation = self._conversations.load(conversation_id)
        except ConversationRepositoryError as exc:
            raise AgentServiceError(str(exc)) from exc
        self._enforce_owner(conversation, principal)
        conversation.add_user(message)
        return conversation

    @staticmethod
    def _enforce_owner(
        conversation: Conversation, principal: str | None
    ) -> None:
        """Bind a session to its first authenticated writer (ADR 0008).

        Principal None means no enforcement (auth disabled, dev mode):
        the owner is left untouched. Otherwise an unowned conversation
        is claimed by this principal (persisted by the caller's save),
        and a conversation owned by someone else rejects this caller.
        The error message names no principal — identity is not leaked
        into error text.
        """
        if principal is None:
            return
        if conversation.owner is None:
            conversation.owner = principal
        elif conversation.owner != principal:
            raise ForbiddenError("session belongs to a different user")

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
            id=conversation.id,
            turns=list(conversation.turns[-MAX_HISTORY_TURNS:]),
            owner=conversation.owner,
        )

    def reset_session(
        self, conversation_id: str, principal: str | None = None
    ) -> None:
        """Clear a session's history (fresh start for a bricked session).

        Enforces ownership like a turn does: a different principal
        cannot reset someone else's session. A reset by an
        authenticated principal re-binds the cleared session to that
        principal; in dev mode (principal None) it stays unowned.
        """
        with self._session_lock(conversation_id):
            try:
                conversation = self._conversations.load(conversation_id)
            except ConversationRepositoryError as exc:
                raise AgentServiceError(str(exc)) from exc
            self._enforce_owner(conversation, principal)
            self._save(Conversation(id=conversation_id, owner=principal))

    # -- turns --------------------------------------------------------------

    def ask(
        self,
        conversation_id: str,
        message: str,
        model: str | None = None,
        principal: str | None = None,
        user_token: str | None = None,
    ) -> AgentReply:
        """One full agentic turn; returns the reply and the tools it used.

        `model` is an allowlist key (ADR 0007); None means the
        configured model. `principal`/`user_token` are the authenticated
        caller (ADR 0008); principal None means auth is off and no
        ownership is enforced. The token is never logged — it travels
        only inside the per-turn request context around the gateway
        call, where tools that forward it downstream can read it.
        """
        with self._session_lock(conversation_id):
            conversation = self._validated(conversation_id, message, principal)
            # Persist the user turn before the gateway runs (see module
            # docstring): a gateway failure must not diverge backends.
            self._save(conversation)
            try:
                gateway = self._resolve_gateway(model)
                with request_context(principal, user_token):
                    turn = gateway.generate(self._gateway_conversation(conversation))
            except ModelGatewayError as exc:
                raise AgentServiceError(str(exc)) from exc
            conversation.add_assistant(
                turn.content, turn.tool_uses, turn.tool_results
            )
            self._save(conversation)
            return AgentReply(text=turn.content, tool_uses=turn.tool_uses)

    async def ask_stream(
        self,
        conversation_id: str,
        message: str,
        model: str | None = None,
        principal: str | None = None,
        user_token: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Same as ask(), but yields text/tool chunks as they arrive.

        `model` is an allowlist key (ADR 0007); None means the
        configured model. `principal`/`user_token` are the authenticated
        caller (ADR 0008); see ask() for the ownership and token rules.
        The request context wraps the whole stream loop, so the token
        stays available to tools for the full turn and is reset when
        the stream ends or is aborted.
        """
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
                conversation = self._validated(conversation_id, message, principal)
                self._save(conversation)  # user turn, before the gateway
                try:
                    gateway = self._resolve_gateway(model)
                    with request_context(principal, user_token):
                        async for chunk in gateway.stream(
                            self._gateway_conversation(conversation)
                        ):
                            if chunk.kind == "text":
                                texts.append(chunk.payload)
                            elif chunk.kind == "tool":
                                # Nit: tool chunks used to be yielded on
                                # every occurrence while saved history
                                # deduped by name — raw SSE consumers saw
                                # repeats the UI hid. Yield each tool name
                                # once, matching the saved-history dedup.
                                if chunk.payload in tools:
                                    continue
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
            # Readiness probes the configured (default) model; a lazy
            # factory builds it here on first probe. Per-key gateways
            # are probed by their first real call, not by readiness.
            gateway = (
                self._gateway_resolver.gateway_for(None)
                if self._gateway_resolver is not None
                else self._gateway
            )
            assert gateway is not None  # guaranteed by __init__
            gateway.ping()
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
