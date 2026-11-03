"""Service ownership binding + request-context seam (ADR 0008).

Fake ports only — no model, no I/O. The repository here copies on
load/save like the SQL backends and preserves the owner, so a claim
must survive a save/load cycle to count.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_api.domain.conversation import ASSISTANT, Conversation, Turn
from agent_api.platform.request_context import get_request_context
from agent_api.ports.model_gateway import StreamChunk
from agent_api.services.ask_agent import (
    AskAgentService,
    ForbiddenError,
)


class ContextRecordingGateway:
    """Records the request context visible inside each gateway call."""

    def __init__(self):
        self.seen_contexts: list = []
        self.seen_conversations: list[Conversation] = []

    def generate(self, conversation: Conversation) -> Turn:
        self.seen_contexts.append(get_request_context())
        self.seen_conversations.append(conversation)
        return Turn(role=ASSISTANT, content="reply", tool_uses=())

    async def stream(self, conversation: Conversation):
        self.seen_contexts.append(get_request_context())
        self.seen_conversations.append(conversation)
        yield StreamChunk(kind="text", payload="reply")

    def ping(self) -> None:
        return None


class CopyingRepository:
    """Copying store that preserves the owner across save/load."""

    def __init__(self):
        self.store: dict[str, Conversation] = {}

    def load(self, conversation_id: str) -> Conversation:
        existing = self.store.get(conversation_id)
        if existing is None:
            return Conversation(id=conversation_id)
        return Conversation(
            id=existing.id, turns=list(existing.turns), owner=existing.owner
        )

    def save(self, conversation: Conversation) -> None:
        self.store[conversation.id] = Conversation(
            id=conversation.id,
            turns=list(conversation.turns),
            owner=conversation.owner,
        )

    def ping(self) -> None:
        return None


def make_service(**kwargs):
    gateway = kwargs.pop("gateway", ContextRecordingGateway())
    repo = kwargs.pop("repo", CopyingRepository())
    return AskAgentService(gateway=gateway, conversations=repo), gateway, repo


def test_first_writer_claims_session():
    service, _, repo = make_service()
    service.ask("s1", "hi", principal="alice")
    assert repo.load("s1").owner == "alice"


def test_same_principal_can_continue():
    service, _, repo = make_service()
    service.ask("s1", "hi", principal="alice")
    service.ask("s1", "again", principal="alice")
    assert len(repo.load("s1").turns) == 4


def test_different_principal_is_forbidden_and_history_unchanged():
    service, _, repo = make_service()
    service.ask("s1", "hi", principal="alice")
    with pytest.raises(ForbiddenError):
        service.ask("s1", "hi", principal="bob")
    conv = repo.load("s1")
    assert [t.content for t in conv.turns] == ["hi", "reply"]
    assert conv.owner == "alice"


def test_legacy_unowned_session_is_claimable_once():
    _, _, repo = make_service()
    legacy = Conversation(id="s1")
    legacy.add_user("old question")
    repo.save(legacy)
    assert repo.load("s1").owner is None

    service, _, _ = make_service(repo=repo)
    service.ask("s1", "new question", principal="alice")
    assert repo.load("s1").owner == "alice"
    with pytest.raises(ForbiddenError):
        service.ask("s1", "hi", principal="bob")


def test_dev_mode_principal_none_enforces_nothing():
    service, _, repo = make_service()
    service.ask("s1", "hi")
    service.ask("s1", "again")
    assert repo.load("s1").owner is None
    # An owned session is untouched by a dev-mode turn only in the
    # sense that no principal is supplied: the service leaves the
    # stored owner as it is.
    repo.save(Conversation(id="s2", owner="alice"))
    service.ask("s2", "hi")
    assert repo.load("s2").owner == "alice"


def test_stream_enforces_binding():
    service, _, repo = make_service()
    service.ask("s1", "hi", principal="alice")

    async def collect():
        return [c async for c in service.ask_stream("s1", "hi", principal="bob")]

    with pytest.raises(ForbiddenError):
        asyncio.run(collect())
    assert [t.content for t in repo.load("s1").turns] == ["hi", "reply"]


def test_reset_by_other_principal_is_forbidden():
    service, _, repo = make_service()
    service.ask("s1", "hi", principal="alice")
    with pytest.raises(ForbiddenError):
        service.reset_session("s1", principal="bob")
    assert len(repo.load("s1").turns) == 2


def test_reset_by_owner_clears_and_rebinds_to_resetter():
    service, _, repo = make_service()
    service.ask("s1", "hi", principal="alice")
    service.reset_session("s1", principal="alice")
    conv = repo.load("s1")
    assert conv.turns == []
    assert conv.owner == "alice"
    with pytest.raises(ForbiddenError):
        service.ask("s1", "hi", principal="bob")


def test_reset_of_unowned_session_claims_it():
    service, _, repo = make_service()
    service.reset_session("s1", principal="alice")
    assert repo.load("s1").owner == "alice"


def test_reset_in_dev_mode_leaves_session_unowned():
    service, _, repo = make_service()
    service.ask("s1", "hi")
    service.reset_session("s1")
    conv = repo.load("s1")
    assert conv.turns == []
    assert conv.owner is None


def test_request_context_visible_to_gateway_and_reset_after():
    service, gateway, _ = make_service()
    assert get_request_context() is None
    service.ask("s1", "hi", principal="alice", user_token="tok-1")
    assert len(gateway.seen_contexts) == 1
    ctx = gateway.seen_contexts[0]
    assert ctx is not None
    assert ctx.principal == "alice"
    assert ctx.token == "tok-1"
    # Reset by the context manager's finally: nothing leaks past the turn.
    assert get_request_context() is None


def test_request_context_visible_during_stream():
    service, gateway, _ = make_service()

    async def collect():
        return [
            c
            async for c in service.ask_stream(
                "s1", "hi", principal="alice", user_token="tok-2"
            )
        ]

    asyncio.run(collect())
    assert len(gateway.seen_contexts) == 1
    ctx = gateway.seen_contexts[0]
    assert ctx is not None
    assert ctx.principal == "alice"
    assert ctx.token == "tok-2"
    assert get_request_context() is None


def test_dev_mode_context_carries_no_identity():
    service, gateway, _ = make_service()
    service.ask("s1", "hi")
    ctx = gateway.seen_contexts[0]
    assert ctx is not None
    assert ctx.principal is None
    assert ctx.token is None


def test_gateway_handoff_copy_preserves_owner():
    service, gateway, _ = make_service()
    service.ask("s1", "hi", principal="alice")
    assert gateway.seen_conversations[0].owner == "alice"
