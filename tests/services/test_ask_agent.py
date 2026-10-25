"""Service tests with fake ports (§9): no model, no I/O, pure orchestration logic."""

from __future__ import annotations

import pytest

from agent_api.domain.conversation import ASSISTANT, Conversation, Turn
from agent_api.ports.model_gateway import ModelGatewayError, StreamChunk
from agent_api.services.ask_agent import (
    AgentServiceError,
    AskAgentService,
    InvalidMessageError,
)


class FakeGateway:
    """In-memory ModelGateway: records conversations, returns canned turns."""

    def __init__(self, reply: str = "canned reply", tools: tuple[str, ...] = ("calculate",)):
        self.seen: list[Conversation] = []
        self.reply = reply
        self.tools = tools
        self.fail = False

    def generate(self, conversation: Conversation) -> Turn:
        self.seen.append(conversation)
        if self.fail:
            raise ModelGatewayError("boom")
        return Turn(role=ASSISTANT, content=self.reply, tool_uses=self.tools)

    async def stream(self, conversation: Conversation):
        self.seen.append(conversation)
        if self.fail:
            raise ModelGatewayError("boom")
        yield StreamChunk(kind="text", payload=self.reply)
        for tool in self.tools:
            yield StreamChunk(kind="tool", payload=tool)

    def ping(self) -> None:
        if self.fail:
            raise ModelGatewayError("boom")


class FakeRepository:
    """In-memory ConversationRepository."""

    def __init__(self):
        self.store: dict[str, Conversation] = {}

    def load(self, conversation_id: str) -> Conversation:
        return self.store.setdefault(conversation_id, Conversation(id=conversation_id))

    def save(self, conversation: Conversation) -> None:
        self.store[conversation.id] = conversation

    def ping(self) -> None:
        return None


def make_service(**kwargs) -> tuple[AskAgentService, FakeGateway, FakeRepository]:
    gateway = kwargs.pop("gateway", FakeGateway())
    repo = kwargs.pop("repo", FakeRepository())
    return AskAgentService(gateway=gateway, conversations=repo), gateway, repo


def test_ask_returns_reply_and_tools():
    service, _, _ = make_service()
    reply = service.ask("s1", "What is 6 * 7?")
    assert reply.text == "canned reply"
    assert reply.tool_uses == ("calculate",)


def test_ask_records_history():
    service, _, repo = make_service()
    service.ask("s1", "first")
    service.ask("s1", "second")
    conv = repo.load("s1")
    assert [t.content for t in conv.turns] == ["first", "canned reply", "second", "canned reply"]


def test_ask_rejects_blank_message():
    service, _, _ = make_service()
    with pytest.raises(InvalidMessageError):
        service.ask("s1", "   ")


def test_ask_translates_gateway_errors():
    gateway = FakeGateway()
    gateway.fail = True
    service, _, _ = make_service(gateway=gateway)
    with pytest.raises(AgentServiceError):
        service.ask("s1", "hello")


def test_ask_stream_yields_chunks_and_saves_history():
    async def collect():
        service, _, repo = make_service()
        chunks = [c async for c in service.ask_stream("s1", "hi")]
        return chunks, repo

    import asyncio

    chunks, repo = asyncio.run(collect())
    assert [(c.kind, c.payload) for c in chunks] == [
        ("text", "canned reply"),
        ("tool", "calculate"),
    ]
    conv = repo.load("s1")
    assert conv.turns[-1].content == "canned reply"
    assert conv.turns[-1].tool_uses == ("calculate",)


def test_readiness_ok_when_dependencies_healthy():
    service, _, _ = make_service()
    report = service.readiness()
    assert report.ready is True
    assert report.failing == ()


def test_readiness_reports_failing_model():
    gateway = FakeGateway()
    gateway.fail = True
    service, _, _ = make_service(gateway=gateway)
    report = service.readiness()
    assert report.ready is False
    assert any("model" in f for f in report.failing)


# --- M-15 / M-17 / M-3 / M-14 additions ---

from agent_api.domain.conversation import ToolResult
from agent_api.ports.capability_repository import CapabilityRepositoryError
from agent_api.ports.conversation_repository import ConversationRepositoryError
from agent_api.ports.embedding_gateway import EmbeddingGatewayError
from agent_api.services.ask_agent import (
    MAX_HISTORY_TURNS,
    PARTIAL_MARKER,
    ReadinessReport,
)


class CopyingRepository:
    """A store that returns copies on load, like sqlite/postgres (unlike
    the shared-object memory store): a turn is only kept if saved."""

    def __init__(self):
        self.store: dict[str, Conversation] = {}

    def load(self, conversation_id: str) -> Conversation:
        existing = self.store.get(conversation_id)
        if existing is None:
            return Conversation(id=conversation_id)
        return Conversation(id=existing.id, turns=list(existing.turns))

    def save(self, conversation: Conversation) -> None:
        self.store[conversation.id] = Conversation(
            id=conversation.id, turns=list(conversation.turns)
        )

    def ping(self) -> None:
        return None


class FailingRepository(CopyingRepository):
    def __init__(self, fail_on="save"):
        super().__init__()
        self.fail_on = fail_on

    def load(self, conversation_id: str) -> Conversation:
        if self.fail_on == "load":
            raise ConversationRepositoryError("db down")
        return super().load(conversation_id)

    def save(self, conversation: Conversation) -> None:
        if self.fail_on == "save":
            raise ConversationRepositoryError("db down")
        super().save(conversation)

    def ping(self) -> None:
        raise ConversationRepositoryError("db down")


class StreamFailGateway(FakeGateway):
    async def stream(self, conversation: Conversation):
        self.seen.append(conversation)
        yield StreamChunk(kind="text", payload="partial answer")
        raise ModelGatewayError("boom mid-stream")


class ToolResultGateway(FakeGateway):
    def generate(self, conversation: Conversation) -> Turn:
        self.seen.append(conversation)
        return Turn(
            role=ASSISTANT,
            content="Gold tier",
            tool_uses=("execute_capability",),
            tool_results=(ToolResult(tool="execute_capability", result='{"tier": "Gold"}'),),
        )


def test_failed_turn_keeps_user_message_on_copying_store():
    # M-15: with the user turn saved before the gateway runs, a failed
    # turn leaves the user message persisted (old code lost it on SQL
    # backends, kept it on memory — backends diverged).
    gateway = FakeGateway()
    gateway.fail = True
    repo = CopyingRepository()
    service = AskAgentService(gateway=gateway, conversations=repo)
    with pytest.raises(AgentServiceError):
        service.ask("s1", "hello")
    conv = repo.load("s1")
    assert [t.content for t in conv.turns] == ["hello"]


def test_stream_failure_persists_partial_with_marker():
    repo = CopyingRepository()
    service = AskAgentService(gateway=StreamFailGateway(), conversations=repo)

    async def collect():
        chunks = []
        try:
            async for chunk in service.ask_stream("s1", "hi"):
                chunks.append(chunk)
        except AgentServiceError:
            return chunks
        raise AssertionError("expected AgentServiceError")

    import asyncio

    chunks = asyncio.run(collect())
    assert [c.payload for c in chunks if c.kind == "text"] == ["partial answer"]
    conv = repo.load("s1")
    assert len(conv.turns) == 2
    assert conv.turns[0].content == "hi"
    assert "partial answer" in conv.turns[1].content
    assert PARTIAL_MARKER in conv.turns[1].content


def test_stream_abort_persists_partial():
    repo = CopyingRepository()
    service = AskAgentService(gateway=FakeGateway(), conversations=repo)

    async def abort_after_first():
        gen = service.ask_stream("s1", "hi")
        first = await gen.__anext__()
        await gen.aclose()
        return first

    import asyncio

    first = asyncio.run(abort_after_first())
    assert first.payload == "canned reply"
    conv = repo.load("s1")
    assert len(conv.turns) == 2
    assert conv.turns[0].content == "hi"
    assert PARTIAL_MARKER in conv.turns[1].content


def test_history_cap_at_gateway_handoff():
    # M-17: N+1 stored turns -> gateway receives at most N, newest last.
    repo = CopyingRepository()
    gateway = FakeGateway()
    service = AskAgentService(gateway=gateway, conversations=repo)
    conv = Conversation(id="s1")
    for i in range(MAX_HISTORY_TURNS + 5):
        conv.add_user(f"q{i}")
        conv.add_assistant(f"a{i}")
    repo.save(conv)
    service.ask("s1", "new question")
    seen = gateway.seen[-1]
    assert len(seen.turns) <= MAX_HISTORY_TURNS
    assert seen.turns[-1].content == "new question"
    # Stored history is NOT truncated — only the handoff window is.
    stored = repo.load("s1")
    assert len(stored.turns) == 2 * (MAX_HISTORY_TURNS + 5) + 2


def test_store_failures_become_service_errors():
    # M-3: a typed store error is caught like a model error, not leaked.
    service = AskAgentService(gateway=FakeGateway(), conversations=FailingRepository("save"))
    with pytest.raises(AgentServiceError, match="db down"):
        service.ask("s1", "hi")
    service = AskAgentService(gateway=FakeGateway(), conversations=FailingRepository("load"))
    with pytest.raises(AgentServiceError, match="db down"):
        service.ask("s1", "hi")


def test_readiness_reports_embedding_and_registry():
    class BadEmbedding:
        def ping(self):
            raise EmbeddingGatewayError("embeddings down")

    class BadRegistry:
        def ping(self):
            raise CapabilityRepositoryError("registry unreadable")

    service = AskAgentService(
        gateway=FakeGateway(),
        conversations=FakeRepository(),
        embedding_gateway=BadEmbedding(),
        capability_registry=BadRegistry(),
    )
    report = service.readiness()
    assert report.ready is False
    assert any("embedding" in f for f in report.failing)
    assert any("registry" in f for f in report.failing)


def test_readiness_reports_typed_store_error():
    service = AskAgentService(
        gateway=FakeGateway(), conversations=FailingRepository("ping")
    )
    report = service.readiness()
    assert report.ready is False
    assert any("store" in f for f in report.failing)


def test_reset_session_clears_history():
    service, _, repo = make_service()
    service.ask("s1", "first")
    assert len(repo.load("s1").turns) == 2
    service.reset_session("s1")
    assert repo.load("s1").turns == []


def test_tool_results_persisted_in_turn():
    repo = CopyingRepository()
    service = AskAgentService(gateway=ToolResultGateway(), conversations=repo)
    service.ask("s1", "what tier?")
    conv = repo.load("s1")
    assert conv.turns[-1].tool_results == (
        ToolResult(tool="execute_capability", result='{"tier": "Gold"}'),
    )


def test_concurrent_turns_on_one_session_serialize():
    # M-15 lost-update race: two threads turning on one session must not
    # overwrite each other; the per-session lock serializes them.
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    class SlowGateway(FakeGateway):
        def __init__(self):
            super().__init__()
            self._guard = threading.Lock()
            self.current = 0
            self.max_seen = 0

        def generate(self, conversation: Conversation) -> Turn:
            with self._guard:
                self.current += 1
                self.max_seen = max(self.max_seen, self.current)
            time.sleep(0.05)
            with self._guard:
                self.current -= 1
            return Turn(role=ASSISTANT, content="reply", tool_uses=())

    gateway = SlowGateway()
    repo = CopyingRepository()
    service = AskAgentService(gateway=gateway, conversations=repo)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda i: service.ask("s1", f"q{i}"), range(2)))
    assert gateway.max_seen == 1
    assert len(repo.load("s1").turns) == 4


def test_stream_tool_result_chunks_persisted():
    # M-13 via streaming: gateways emit tool_result chunks after text;
    # the service stores them on the assistant turn.
    class ResultStreamGateway(FakeGateway):
        async def stream(self, conversation: Conversation):
            self.seen.append(conversation)
            yield StreamChunk(kind="text", payload="Gold tier")
            yield StreamChunk(kind="tool", payload="execute_capability")
            yield StreamChunk(
                kind="tool_result",
                payload='{"tool": "execute_capability", "result": "{\\"tier\\": \\"Gold\\"}"}',
            )

    repo = CopyingRepository()
    service = AskAgentService(gateway=ResultStreamGateway(), conversations=repo)

    async def collect():
        return [c async for c in service.ask_stream("s1", "hi")]

    import asyncio

    asyncio.run(collect())
    conv = repo.load("s1")
    assert conv.turns[-1].tool_results == (
        ToolResult(tool="execute_capability", result='{"tier": "Gold"}'),
    )
