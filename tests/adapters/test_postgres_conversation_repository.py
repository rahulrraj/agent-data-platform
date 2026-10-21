"""Tests for the Postgres conversation repository.

No live Postgres here: psycopg is faked at the module boundary. What is
verified — SQL shape (%s placeholders, JSONB cast, upsert), serialization
round-trip, empty-load semantics, and the helpful missing-driver error.
"""

from __future__ import annotations

import builtins
import json

import pytest

from agent_api.adapters import postgres_conversation_repository as pg_mod
from agent_api.adapters.postgres_conversation_repository import (
    PostgresConversationRepository,
)
from agent_api.domain.conversation import Conversation, Turn


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._row = None

    def execute(self, sql, params=None):
        self._conn.statements.append((sql, params))
        normalized = " ".join(sql.split()).upper()
        if normalized.startswith("SELECT TURNS_JSON"):
            # emulate psycopg returning JSONB as native Python objects
            stored = self._conn.data.get(params[0])
            self._row = (json.loads(stored),) if stored is not None else None
        elif normalized.startswith("INSERT"):
            self._conn.data[params[0]] = params[1]
        return self

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self):
        self.data: dict = {}
        self.statements: list = []

    def execute(self, sql, params=None):
        return FakeCursor(self).execute(sql, params)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakePsycopg:
    def __init__(self):
        self.conn = FakeConn()
        self.dsns: list = []

    def connect(self, dsn):
        self.dsns.append(dsn)
        return self.conn


@pytest.fixture()
def fake_pg(monkeypatch):
    fake = FakePsycopg()
    monkeypatch.setattr(pg_mod, "_psycopg", lambda: fake)
    return fake


def _conversation():
    return Conversation(
        id="s1",
        turns=[
            Turn(role="user", content="hi", tool_uses=()),
            Turn(role="assistant", content="hello", tool_uses=("calculate",)),
        ],
    )


def test_save_then_load_round_trip(fake_pg):
    repo = PostgresConversationRepository("postgres://test")
    repo.save(_conversation())
    loaded = repo.load("s1")
    assert loaded.id == "s1"
    assert [(t.role, t.content, t.tool_uses) for t in loaded.turns] == [
        ("user", "hi", ()),
        ("assistant", "hello", ("calculate",)),
    ]


def test_load_missing_returns_empty_conversation(fake_pg):
    repo = PostgresConversationRepository("postgres://test")
    loaded = repo.load("nope")
    assert loaded.id == "nope"
    assert loaded.turns == []


def test_save_upserts(fake_pg):
    repo = PostgresConversationRepository("postgres://test")
    repo.save(_conversation())
    repo.save(Conversation(id="s1", turns=[Turn(role="user", content="v2", tool_uses=())]))
    assert repo.load("s1").turns[0].content == "v2"
    insert_sql = " ".join(
        sql for sql, _ in fake_pg.conn.statements if "INSERT" in sql.upper()
    )
    assert "ON CONFLICT (id) DO UPDATE" in insert_sql
    assert "%s::jsonb" in insert_sql


def test_schema_created_on_init(fake_pg):
    PostgresConversationRepository("postgres://test")
    ddl = " ".join(sql for sql, _ in fake_pg.conn.statements)
    assert "CREATE TABLE IF NOT EXISTS conversations" in ddl
    assert "JSONB" in ddl


def test_empty_dsn_raises(fake_pg):
    with pytest.raises(ValueError, match="POSTGRES_DSN"):
        PostgresConversationRepository("")


def test_missing_driver_error(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "psycopg":
            raise ImportError("nope")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match=r"pip install"):
        PostgresConversationRepository("postgres://test")


def test_ping(fake_pg):
    PostgresConversationRepository("postgres://test").ping()
    assert any("SELECT 1" in sql for sql, _ in fake_pg.conn.statements)
