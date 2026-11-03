"""In-memory conversation repository: owner travels with the record.

The memory adapter stores Conversation objects directly, so no
serialization change was needed for ADR 0008 — these tests pin the
round-trip behavior alongside the SQL adapters'.
"""

from __future__ import annotations

from agent_api.adapters.in_memory_conversation_repository import (
    InMemoryConversationRepository,
)
from agent_api.domain.conversation import Conversation


def test_owner_round_trip():
    repo = InMemoryConversationRepository()
    conv = Conversation(id="s1", owner="alice")
    conv.add_user("hi")
    repo.save(conv)
    loaded = repo.load("s1")
    assert loaded.owner == "alice"
    assert [t.content for t in loaded.turns] == ["hi"]


def test_unknown_session_loads_unowned():
    repo = InMemoryConversationRepository()
    assert repo.load("nope").owner is None


def test_domain_conversation_owner_defaults_to_none():
    assert Conversation(id="s").owner is None
