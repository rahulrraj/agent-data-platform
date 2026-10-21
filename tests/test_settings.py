"""Settings parsing: store selection and Postgres DSN."""

from __future__ import annotations

import pytest

from agent_api.config.settings import Settings


def test_postgres_store_settings(monkeypatch):
    monkeypatch.setenv("CONVERSATION_STORE", "postgres")
    monkeypatch.setenv("POSTGRES_DSN", "postgres://u:p@host/db")
    settings = Settings.from_env()
    assert settings.conversation_store == "postgres"
    assert settings.postgres_dsn == "postgres://u:p@host/db"


def test_bad_store_rejected(monkeypatch):
    monkeypatch.setenv("CONVERSATION_STORE", "dynamo")
    with pytest.raises(ValueError, match="CONVERSATION_STORE"):
        Settings.from_env()
