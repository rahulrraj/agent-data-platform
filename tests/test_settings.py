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


def test_empty_data_connections_means_empty_dict(monkeypatch):
    # M-21: an unset secret expanding to "" must not crash startup.
    monkeypatch.setenv("DATA_CONNECTIONS", "")
    assert Settings.from_env().data_connections == {}
    monkeypatch.setenv("DATA_CONNECTIONS", "   ")
    assert Settings.from_env().data_connections == {}


def test_cors_origins_parsing(monkeypatch):
    assert Settings.from_env().cors_origins == ()
    monkeypatch.setenv("CORS_ORIGINS", "https://ui.example, https://app.example ,")
    assert Settings.from_env().cors_origins == (
        "https://ui.example",
        "https://app.example",
    )


# --- m-13 numeric parsing + RETRY_MAX_DELAY nit (2026-10-25) ---

def test_bad_numeric_env_names_the_variable(monkeypatch):
    monkeypatch.setenv("RETRY_MAX_ATTEMPTS", "abc")
    with pytest.raises(ValueError, match="RETRY_MAX_ATTEMPTS"):
        Settings.from_env()
    monkeypatch.setenv("RETRY_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("PORT", "abc")
    with pytest.raises(ValueError, match="PORT"):
        Settings.from_env()


def test_numeric_ranges_enforced(monkeypatch):
    monkeypatch.setenv("RETRY_MAX_ATTEMPTS", "0")
    with pytest.raises(ValueError, match="RETRY_MAX_ATTEMPTS"):
        Settings.from_env()
    monkeypatch.setenv("RETRY_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("RETRY_BASE_DELAY", "-0.5")
    with pytest.raises(ValueError, match="RETRY_BASE_DELAY"):
        Settings.from_env()
    monkeypatch.setenv("RETRY_BASE_DELAY", "0.5")
    monkeypatch.setenv("PORT", "70000")
    with pytest.raises(ValueError, match="PORT"):
        Settings.from_env()
    monkeypatch.setenv("PORT", "8000")
    monkeypatch.setenv("RETRY_MAX_DELAY", "-1")
    with pytest.raises(ValueError, match="RETRY_MAX_DELAY"):
        Settings.from_env()


def test_retry_max_delay_configurable(monkeypatch):
    monkeypatch.setenv("RETRY_MAX_DELAY", "2.5")
    assert Settings.from_env().retry_max_delay == 2.5
    assert Settings.from_env().retry_max_delay == 2.5  # default is 8.0, see below
    monkeypatch.delenv("RETRY_MAX_DELAY")
    assert Settings.from_env().retry_max_delay == 8.0


# --- ADR 0007: ALLOWED_MODELS allowlist ---

def test_allowed_models_unset_is_empty(monkeypatch):
    monkeypatch.delenv("ALLOWED_MODELS", raising=False)
    assert Settings.from_env().allowed_models == {}
    monkeypatch.setenv("ALLOWED_MODELS", "  ")
    assert Settings.from_env().allowed_models == {}


def test_allowed_models_parsed(monkeypatch):
    monkeypatch.setenv(
        "ALLOWED_MODELS",
        '{"sonnet": {"provider": "bedrock", "model": "us.anthropic.claude-sonnet-5-5"},'
        ' "flash": {"provider": "gemini", "model": "gemini-flash-latest"}}',
    )
    allowed = Settings.from_env().allowed_models
    assert allowed["sonnet"].provider == "bedrock"
    assert allowed["sonnet"].model == "us.anthropic.claude-sonnet-5-5"
    assert allowed["flash"].provider == "gemini"
    assert allowed["flash"].model == "gemini-flash-latest"


def test_allowed_models_bad_json_rejected(monkeypatch):
    monkeypatch.setenv("ALLOWED_MODELS", "{not json")
    with pytest.raises(ValueError, match="ALLOWED_MODELS"):
        Settings.from_env()


def test_allowed_models_must_be_an_object(monkeypatch):
    monkeypatch.setenv("ALLOWED_MODELS", '[{"provider": "gemini"}]')
    with pytest.raises(ValueError, match="ALLOWED_MODELS"):
        Settings.from_env()


def test_allowed_models_bad_provider_rejected(monkeypatch):
    monkeypatch.setenv(
        "ALLOWED_MODELS", '{"x": {"provider": "azure", "model": "gpt-4"}}'
    )
    with pytest.raises(ValueError, match="ALLOWED_MODELS"):
        Settings.from_env()


def test_allowed_models_empty_model_rejected(monkeypatch):
    monkeypatch.setenv(
        "ALLOWED_MODELS", '{"x": {"provider": "gemini", "model": ""}}'
    )
    with pytest.raises(ValueError, match="ALLOWED_MODELS"):
        Settings.from_env()
    monkeypatch.setenv("ALLOWED_MODELS", '{"x": {"provider": "gemini"}}')
    with pytest.raises(ValueError, match="ALLOWED_MODELS"):
        Settings.from_env()


def test_allowed_models_reserved_default_key_rejected(monkeypatch):
    monkeypatch.setenv(
        "ALLOWED_MODELS",
        '{"default": {"provider": "gemini", "model": "gemini-flash-latest"}}',
    )
    with pytest.raises(ValueError, match='reserved key "default"'):
        Settings.from_env()


def test_allowed_models_empty_key_rejected(monkeypatch):
    monkeypatch.setenv(
        "ALLOWED_MODELS", '{"": {"provider": "gemini", "model": "m"}}'
    )
    with pytest.raises(ValueError, match="ALLOWED_MODELS"):
        Settings.from_env()


def test_allowed_models_non_object_entry_rejected(monkeypatch):
    monkeypatch.setenv("ALLOWED_MODELS", '{"x": "gemini-flash-latest"}')
    with pytest.raises(ValueError, match="ALLOWED_MODELS"):
        Settings.from_env()
