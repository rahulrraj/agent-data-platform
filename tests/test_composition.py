"""Composition root (M-2): Lambda and CLI share one builder, no cycle."""

from __future__ import annotations

import os

import pytest

from agent_api import composition
from agent_api.adapters.ollama_model_gateway import OllamaModelGateway
from agent_api.config.settings import Settings
from agent_api.controllers import lambda_handler
from agent_api.platform.retry import RetryPolicy


def test_lambda_and_main_share_build_app():
    assert lambda_handler.build_app is composition.build_app
    from agent_api import main

    assert main.build_app is composition.build_app


def test_build_gateway_strips_proxy_before_import(monkeypatch):
    # M-1: the composition root owns proxy handling; adapters don't.
    monkeypatch.setenv("http_proxy", "http://broken:1]")
    monkeypatch.setenv("MY_PROXY", "x")
    settings = Settings.from_env()
    gateway = composition.build_gateway(
        settings, RetryPolicy(), tools=[], system_prompt="s"
    )
    assert isinstance(gateway, OllamaModelGateway)
    assert "http_proxy" not in os.environ
    assert "MY_PROXY" not in os.environ


# --- m-11 Lambda enforcement (2026-10-25) ---

def test_lambda_requires_postgres_and_tmp_cache(monkeypatch):
    from agent_api.composition import _validate_lambda_settings

    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "agent-api")
    monkeypatch.setenv("CONVERSATION_STORE", "memory")
    settings = Settings.from_env()
    assert settings.lambda_runtime is True
    with pytest.raises(RuntimeError, match="CONVERSATION_STORE must be 'postgres'"):
        _validate_lambda_settings(settings)
    monkeypatch.setenv("CONVERSATION_STORE", "postgres")
    monkeypatch.setenv("POSTGRES_DSN", "postgres://u:p@host/db")
    monkeypatch.setenv("EMBEDDING_CACHE_PATH", "embedding_cache.db")
    with pytest.raises(RuntimeError, match="EMBEDDING_CACHE_PATH"):
        _validate_lambda_settings(Settings.from_env())
    monkeypatch.setenv("EMBEDDING_CACHE_PATH", "/tmp/embedding_cache.db")
    _validate_lambda_settings(Settings.from_env())  # passes


def test_non_lambda_store_unrestricted(monkeypatch):
    from agent_api.composition import _validate_lambda_settings

    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    settings = Settings.from_env()
    assert settings.lambda_runtime is False
    _validate_lambda_settings(settings)  # memory store is fine outside Lambda
