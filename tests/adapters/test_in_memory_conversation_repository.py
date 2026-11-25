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


# --- per-user history: list_sessions ---

def _seed(repo):
    for sid, owner in (("a", "alice"), ("b", "bob"), ("c", None)):
        conv = Conversation(id=sid, owner=owner)
        conv.add_user("hi")
        repo.save(conv)


def test_list_sessions_returns_only_own():
    repo = InMemoryConversationRepository()
    _seed(repo)
    assert [(s.id, s.owner) for s in repo.list_sessions("alice")] == [
        ("a", "alice")
    ]
    assert [s.id for s in repo.list_sessions("bob")] == ["b"]


def test_list_sessions_dev_mode_lists_all_including_unowned():
    repo = InMemoryConversationRepository()
    _seed(repo)
    assert sorted(s.id for s in repo.list_sessions(None)) == ["a", "b", "c"]


def test_list_sessions_empty_when_no_match():
    repo = InMemoryConversationRepository()
    _seed(repo)
    assert repo.list_sessions("carol") == []
