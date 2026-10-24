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


def test_bedrock_provider_defaults(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "bedrock")
    settings = Settings.from_env()
    assert settings.provider == "bedrock"
    assert settings.aws_region == "us-east-1"
    assert settings.bedrock_model == "us.anthropic.claude-sonnet-5-5"
    assert settings.bedrock_embed_model == "amazon.titan-embed-text-v2:0"
    assert settings.describe_model() == "strands:bedrock:us.anthropic.claude-sonnet-5-5"


def test_bedrock_provider_overrides(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "bedrock")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "us.anthropic.claude-opus-5-5")
    monkeypatch.setenv("BEDROCK_EMBED_MODEL", "cohere.embed-english-v3")
    settings = Settings.from_env()
    assert settings.aws_region == "eu-west-1"
    assert settings.bedrock_model == "us.anthropic.claude-opus-5-5"
    assert settings.bedrock_embed_model == "cohere.embed-english-v3"


def test_bad_provider_rejected(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "azure")
    with pytest.raises(ValueError, match="AGENT_MODEL_PROVIDER"):
        Settings.from_env()
