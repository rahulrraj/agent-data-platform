"""Adapter tests for the SQLite conversation store: round-trips on a temp file."""

import pytest

from agent_api.adapters.sqlite_conversation_repository import SqliteConversationRepository
from agent_api.domain.conversation import Conversation


@pytest.fixture()
def repo(tmp_path):
    return SqliteConversationRepository(str(tmp_path / "test.db"))


def test_load_returns_empty_conversation_for_unknown_id(repo):
    conv = repo.load("nope")
    assert conv.id == "nope"
    assert conv.turns == []


def test_save_and_load_roundtrip(repo):
    conv = repo.load("s1")
    conv.add_user("What is 6 * 7?")
    conv.add_assistant("42", ["calculate"])
    repo.save(conv)

    loaded = repo.load("s1")
    assert [t.content for t in loaded.turns] == ["What is 6 * 7?", "42"]
    assert loaded.turns[1].tool_uses == ("calculate",)


def test_overwrite_replaces_history(repo):
    conv = repo.load("s1")
    conv.add_user("first")
    repo.save(conv)
    conv2 = repo.load("s1")
    assert len(conv2.turns) == 1  # fresh object, not the mutated one


def test_ping_ok(repo):
    repo.ping()  # raises nothing


def test_data_survives_reopen(tmp_path):
    path = str(tmp_path / "test.db")
    repo = SqliteConversationRepository(path)
    conv = repo.load("s1")
    conv.add_user("persist me")
    repo.save(conv)

    repo2 = SqliteConversationRepository(path)
    assert [t.content for t in repo2.load("s1").turns] == ["persist me"]


def test_tool_results_round_trip(tmp_path):
    # M-13: tool results survive a save/load cycle.
    from agent_api.domain.conversation import ASSISTANT, ToolResult, USER

    repo = SqliteConversationRepository(str(tmp_path / "c.db"))
    conv = Conversation(id="s")
    conv.add_user("what tier?")
    conv.add_assistant(
        "Gold tier",
        ("execute_capability",),
        (ToolResult(tool="execute_capability", result='{"tier": "Gold"}'),),
    )
    repo.save(conv)
    loaded = repo.load("s")
    assert loaded.turns[1].tool_results == (
        ToolResult(tool="execute_capability", result='{"tier": "Gold"}'),
    )


# --- m-17 corrupt-history fallback (2026-10-25) ---

def test_corrupt_turns_json_returns_empty_conversation(tmp_path):
    from agent_api.adapters.sqlite_conversation_repository import (
        SqliteConversationRepository,
    )

    repo = SqliteConversationRepository(str(tmp_path / "conv.db"))
    import sqlite3

    with sqlite3.connect(str(tmp_path / "conv.db")) as conn:
        conn.execute(
            "INSERT INTO conversations (id, turns_json) VALUES (?, ?)",
            ("broken", "{not valid json"),
        )
        conn.commit()
    assert repo.load("broken").turns == []
    # And the session is recoverable: the next save overwrites the row.
    from agent_api.domain.conversation import Conversation

    repo.save(Conversation(id="broken", turns=[]))
    assert repo.load("broken").turns == []


def test_malformed_turn_shape_returns_empty_conversation(tmp_path):
    from agent_api.adapters.sqlite_conversation_repository import (
        SqliteConversationRepository,
    )

    repo = SqliteConversationRepository(str(tmp_path / "conv.db"))
    import sqlite3

    with sqlite3.connect(str(tmp_path / "conv.db")) as conn:
        conn.execute(
            "INSERT INTO conversations (id, turns_json) VALUES (?, ?)",
            ("broken", '[{"role": "user"}]'),  # missing "content"
        )
        conn.commit()
    assert repo.load("broken").turns == []


# --- ADR 0008: conversation owner round-trips in the same column ---

def test_owner_round_trip(repo):
    conv = repo.load("s1")
    conv.owner = "alice"
    conv.add_user("hi")
    repo.save(conv)
    loaded = repo.load("s1")
    assert loaded.owner == "alice"
    assert [t.content for t in loaded.turns] == ["hi"]


def test_legacy_bare_list_row_loads_with_no_owner(tmp_path):
    # Rows written before ownership existed hold a bare turns list:
    # they deserialize with owner None (claimable once, then bound).
    import json
    import sqlite3

    repo = SqliteConversationRepository(str(tmp_path / "legacy.db"))
    legacy = json.dumps([{"role": "user", "content": "old", "tool_uses": []}])
    with sqlite3.connect(str(tmp_path / "legacy.db")) as conn:
        conn.execute(
            "INSERT INTO conversations (id, turns_json) VALUES (?, ?)",
            ("old-session", legacy),
        )
        conn.commit()
    loaded = repo.load("old-session")
    assert loaded.owner is None
    assert [t.content for t in loaded.turns] == ["old"]
    # The next save rewrites the row in envelope form, claiming it.
    loaded.owner = "alice"
    repo.save(loaded)
    assert repo.load("old-session").owner == "alice"


# --- per-user history: list_sessions ---

def _seed_list(repo):
    for sid, owner in (("a", "alice"), ("b", "bob"), ("c", None)):
        conv = Conversation(id=sid, owner=owner)
        conv.add_user("hi")
        repo.save(conv)


def test_list_sessions_returns_only_own(repo):
    _seed_list(repo)
    assert [(s.id, s.owner) for s in repo.list_sessions("alice")] == [
        ("a", "alice")
    ]
    assert [s.id for s in repo.list_sessions("bob")] == ["b"]
    assert repo.list_sessions("carol") == []


def test_list_sessions_dev_mode_lists_all_including_unowned(repo):
    _seed_list(repo)
    assert sorted(s.id for s in repo.list_sessions(None)) == ["a", "b", "c"]
    # No updated_at column in this store: summaries carry None.
    assert all(s.updated_at is None for s in repo.list_sessions(None))


def test_list_sessions_legacy_row_lists_in_dev_only(tmp_path):
    import json
    import sqlite3

    repo = SqliteConversationRepository(str(tmp_path / "legacy-list.db"))
    _seed_list(repo)
    legacy = json.dumps([{"role": "user", "content": "old", "tool_uses": []}])
    with sqlite3.connect(str(tmp_path / "legacy-list.db")) as conn:
        conn.execute(
            "INSERT INTO conversations (id, turns_json) VALUES (?, ?)",
            ("old-session", legacy),
        )
        conn.commit()
    # Dev mode lists the ownerless legacy row; an authenticated
    # caller never sees it (unclaimed) or anyone else's sessions.
    assert "old-session" in [s.id for s in repo.list_sessions(None)]
    assert "old-session" not in [s.id for s in repo.list_sessions("alice")]
    assert [s.id for s in repo.list_sessions("alice")] == ["a"]
