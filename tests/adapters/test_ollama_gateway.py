"""Adapter integration test (§9): OllamaModelGateway against a real Ollama server.

Skipped automatically when no Ollama server is reachable. When run, it drives
the real model loop, so it takes ~30s on a small local model.
"""

from __future__ import annotations

import os

import httpx
import pytest

from agent_api.adapters.ollama_model_gateway import OllamaModelGateway
from agent_api.domain.conversation import USER, Conversation, Turn
from agent_api.tools import load_tools, tool_names
from agent_api.domain.prompts import build_system_prompt

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b")


def _ollama_up() -> bool:
    try:
        # trust_env=False: this sandbox's no_proxy values break httpx's
        # proxy parsing, and localhost needs no proxy anyway.
        with httpx.Client(trust_env=False, timeout=3) as client:
            r = client.get(f"{OLLAMA_HOST}/api/tags")
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


needs_ollama = pytest.mark.skipif(not _ollama_up(), reason="Ollama server not reachable")


@needs_ollama
def test_generate_uses_tool_and_answers():
    tools = load_tools()
    gateway = OllamaModelGateway(
        host=OLLAMA_HOST,
        model_id=OLLAMA_MODEL,
        tools=tools,
        system_prompt=build_system_prompt(tool_names(tools)),
    )
    conv = Conversation(id="t", turns=[Turn(role=USER, content="What is 6 * 7? Use the calculate tool.")])
    turn = gateway.generate(conv)
    assert "42" in turn.content, turn.content
    assert "calculate" in turn.tool_uses, turn.tool_uses
