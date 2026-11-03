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


# --- ADR 0007: per-request model selection ---

def _factory_settings(monkeypatch, allowed_models=""):
    from agent_api.config.settings import Settings

    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:1.5b")
    if allowed_models:
        monkeypatch.setenv("ALLOWED_MODELS", allowed_models)
    else:
        monkeypatch.delenv("ALLOWED_MODELS", raising=False)
    return Settings.from_env()


def test_factory_resolves_default_and_keys(monkeypatch):
    from agent_api.adapters.bedrock_model_gateway import BedrockModelGateway
    from agent_api.adapters.gemini_model_gateway import GeminiModelGateway
    from agent_api.adapters.ollama_model_gateway import OllamaModelGateway
    from agent_api.composition import ModelGatewayFactory

    settings = _factory_settings(
        monkeypatch,
        '{"sonnet": {"provider": "bedrock", "model": "us.anthropic.claude-sonnet-5-5"},'
        ' "flash": {"provider": "gemini", "model": "gemini-flash-latest"}}',
    )
    factory = ModelGatewayFactory(settings, RetryPolicy(), tools=[], system_prompt="s")
    assert factory.available_keys == ("default", "sonnet", "flash")

    default = factory.gateway_for(None)
    assert isinstance(default, OllamaModelGateway)
    assert default._model_id == "qwen2.5:1.5b"
    assert factory.gateway_for("default") is default

    sonnet = factory.gateway_for("sonnet")
    assert isinstance(sonnet, BedrockModelGateway)
    assert sonnet._model_id == "us.anthropic.claude-sonnet-5-5"
    flash = factory.gateway_for("flash")
    assert isinstance(flash, GeminiModelGateway)
    assert flash._model_id == "gemini-flash-latest"


def test_factory_caches_one_gateway_per_key(monkeypatch):
    from agent_api.composition import ModelGatewayFactory

    settings = _factory_settings(
        monkeypatch, '{"sonnet": {"provider": "bedrock", "model": "m1"}}'
    )
    factory = ModelGatewayFactory(settings, RetryPolicy(), tools=[], system_prompt="s")
    assert factory.gateway_for("sonnet") is factory.gateway_for("sonnet")
    assert factory.gateway_for("sonnet") is not factory.gateway_for("default")


def test_factory_unknown_key_raises_key_error(monkeypatch):
    from agent_api.composition import ModelGatewayFactory

    settings = _factory_settings(monkeypatch)
    factory = ModelGatewayFactory(settings, RetryPolicy(), tools=[], system_prompt="s")
    assert factory.available_keys == ("default",)
    with pytest.raises(KeyError):
        factory.gateway_for("nope")


def test_factory_rejects_langgraph_bedrock_key_at_startup(monkeypatch):
    from agent_api.composition import ModelGatewayFactory

    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.setenv("AGENT_FRAMEWORK", "langgraph")
    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv(
        "ALLOWED_MODELS", '{"sonnet": {"provider": "bedrock", "model": "m1"}}'
    )
    settings = Settings.from_env()
    with pytest.raises(RuntimeError, match="ALLOWED_MODELS"):
        ModelGatewayFactory(settings, RetryPolicy(), tools=[], system_prompt="s")


def test_factory_rejects_langgraph_bedrock_default_at_startup(monkeypatch):
    from agent_api.composition import ModelGatewayFactory

    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.setenv("AGENT_FRAMEWORK", "langgraph")
    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "bedrock")
    settings = Settings.from_env()
    with pytest.raises(RuntimeError, match="LangGraph \\+ Bedrock"):
        ModelGatewayFactory(settings, RetryPolicy(), tools=[], system_prompt="s")


def test_factory_does_not_fail_startup_without_credentials(monkeypatch):
    # ADR 0007: a listed provider without credentials surfaces per
    # request as a 502, never as a startup error. Building the factory
    # and the gateway must succeed with no GEMINI_API_KEY; the
    # readiness ping is what reports the missing key.
    from agent_api.composition import ModelGatewayFactory
    from agent_api.ports.model_gateway import ModelGatewayError

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    settings = _factory_settings(
        monkeypatch, '{"flash": {"provider": "gemini", "model": "m1"}}'
    )
    factory = ModelGatewayFactory(settings, RetryPolicy(), tools=[], system_prompt="s")
    gateway = factory.gateway_for("flash")  # construction succeeds
    with pytest.raises(ModelGatewayError, match="GEMINI_API_KEY"):
        gateway.ping()


def test_build_gateway_still_builds_default(monkeypatch):
    # Backward-compat entry point: the configured gateway, immediately.
    from agent_api.adapters.ollama_model_gateway import OllamaModelGateway

    settings = _factory_settings(monkeypatch)
    gateway = composition.build_gateway(
        settings, RetryPolicy(), tools=[], system_prompt="s"
    )
    assert isinstance(gateway, OllamaModelGateway)


# --- ADR 0008: token validator wiring (composition root only) ---

def test_build_token_validator_none_when_auth_disabled(monkeypatch):
    from agent_api.composition import _build_token_validator

    monkeypatch.setenv("AUTH_DISABLED", "true")
    assert _build_token_validator(Settings.from_env()) is None


def test_build_token_validator_wired_when_issuer_configured(monkeypatch):
    from agent_api.adapters.oidc_token_validator import OidcTokenValidator
    from agent_api.composition import _build_token_validator

    monkeypatch.delenv("AUTH_DISABLED", raising=False)
    monkeypatch.setenv("AUTH_ISSUER", "https://example.okta.com")
    monkeypatch.setenv("AUTH_AUDIENCE", "test-audience")
    validator = _build_token_validator(Settings.from_env())
    assert isinstance(validator, OidcTokenValidator)


def test_build_app_enforces_auth_when_issuer_configured(monkeypatch, tmp_path):
    # End to end through the composed app: with an issuer configured,
    # /api/ask without a bearer token is 401 before any service work
    # (no JWKS fetch happens — validation is never reached).
    from fastapi.testclient import TestClient

    from agent_api.composition import build_app

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("AUTH_DISABLED", raising=False)
    monkeypatch.setenv("AUTH_ISSUER", "https://example.okta.com")
    monkeypatch.setenv("AUTH_AUDIENCE", "test-audience")
    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("CONVERSATION_STORE", "memory")
    monkeypatch.setenv("EMBEDDING_CACHE_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setenv(
        "CAPABILITIES_PATH",
        os.path.join(repo_root, "registry", "capabilities.yaml"),
    )
    client = TestClient(build_app(Settings.from_env()))
    r = client.post("/api/ask", json={"message": "hi", "session_id": "s"})
    assert r.status_code == 401


def test_build_app_auth_disabled_reaches_service(monkeypatch, tmp_path):
    # With the dev opt-out, the same call is NOT a 401: it reaches the
    # service (and fails downstream at the model, which is not the
    # auth layer's concern here).
    from fastapi.testclient import TestClient

    from agent_api.composition import build_app

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("CONVERSATION_STORE", "memory")
    monkeypatch.setenv("EMBEDDING_CACHE_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setenv(
        "CAPABILITIES_PATH",
        os.path.join(repo_root, "registry", "capabilities.yaml"),
    )
    client = TestClient(build_app(Settings.from_env()))
    r = client.post("/api/ask", json={"message": "hi", "session_id": "s"})
    assert r.status_code != 401


def test_build_app_wires_allowlist_and_rejects_unknown_key(monkeypatch, tmp_path):
    # End to end through the composed app: health lists the keys and an
    # unknown model key is a 400 — without ever calling a model.
    from fastapi.testclient import TestClient

    from agent_api.composition import build_app

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("CONVERSATION_STORE", "memory")
    monkeypatch.setenv("EMBEDDING_CACHE_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setenv(
        "CAPABILITIES_PATH",
        os.path.join(repo_root, "registry", "capabilities.yaml"),
    )
    monkeypatch.setenv(
        "ALLOWED_MODELS",
        '{"sonnet": {"provider": "bedrock", "model": "us.anthropic.claude-sonnet-5-5"}}',
    )
    client = TestClient(build_app(Settings.from_env()))
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["models"] == ["default", "sonnet"]
    assert health.json()["default_model"] == "default"

    r = client.post(
        "/api/ask",
        json={"message": "hi", "session_id": "s", "model": "nope"},
    )
    assert r.status_code == 400
    assert "available models: default, sonnet" in r.json()["detail"]

def _forwarding_registry(tmp_path, forwarding: bool):
    registry = tmp_path / "capabilities.yaml"
    registry.write_text(
        "capabilities:\n"
        "  - id: status_check\n"
        "    description: Check status.\n"
        "    kind: http\n"
        "    connection: test_api\n"
        f"    forward_user_token: {'true' if forwarding else 'false'}\n"
        "    request:\n"
        "      path: /status\n"
        "      method: GET\n"
    )
    return str(registry)


def test_build_tool_deps_rejects_forwarding_plus_static_key(monkeypatch, tmp_path):
    # ADR 0008, decision 5: forwarding + a static api_key on the same
    # connection is a startup config error, raised from build_tool_deps.
    import json

    from agent_api.composition import build_tool_deps
    from agent_api.ports.capability_repository import CapabilityRepositoryError

    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("CONVERSATION_STORE", "memory")
    monkeypatch.setenv("EMBEDDING_CACHE_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setenv(
        "CAPABILITIES_PATH", _forwarding_registry(tmp_path, forwarding=True)
    )
    monkeypatch.setenv(
        "DATA_CONNECTIONS",
        json.dumps(
            {"test_api": {"base_url": "https://example.invalid", "api_key": "x"}}
        ),
    )
    with pytest.raises(CapabilityRepositoryError, match="forward_user_token"):
        build_tool_deps(Settings.from_env(), RetryPolicy())


def test_build_tool_deps_allows_forwarding_without_static_key(monkeypatch, tmp_path):
    import json

    from agent_api.composition import build_tool_deps

    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("AGENT_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("CONVERSATION_STORE", "memory")
    monkeypatch.setenv("EMBEDDING_CACHE_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setenv(
        "CAPABILITIES_PATH", _forwarding_registry(tmp_path, forwarding=True)
    )
    monkeypatch.setenv(
        "DATA_CONNECTIONS",
        json.dumps({"test_api": {"base_url": "https://example.invalid"}}),
    )
    deps = build_tool_deps(Settings.from_env(), RetryPolicy())
    assert deps.capability_registry.get("status_check").forward_user_token is True
