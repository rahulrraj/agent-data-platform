"""Composition root (M-2): Lambda and CLI share one builder, no cycle."""

from __future__ import annotations

import os

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
