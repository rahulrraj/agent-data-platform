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
