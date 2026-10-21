"""Adapter integration test (§9): LangGraphOllamaGateway against a real Ollama server.

Skipped automatically when no Ollama server is reachable. When run, it drives
the real model loop, so it takes ~30s on a small local model.
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest

from agent_api.adapters.langgraph_ollama_gateway import LangGraphOllamaGateway
from agent_api.domain.conversation import USER, Conversation, Turn
from agent_api.tools import load_tools, tool_names
from agent_api.domain.prompts import build_system_prompt
from agent_api.platform.retry import RetryPolicy

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b")


def _ollama_up() -> bool:
    try:
        # trust_env=False: the sandbox's no_proxy values break httpx parsing.
        with httpx.Client(trust_env=False, timeout=3) as client:
            return client.get(f"{OLLAMA_HOST}/api/tags").status_code == 200
    except Exception:  # noqa: BLE001
        return False


needs_ollama = pytest.mark.skipif(not _ollama_up(), reason="Ollama server not reachable")


def _gateway():
    tools = load_tools()
    return LangGraphOllamaGateway(
        host=OLLAMA_HOST,
        model_id=OLLAMA_MODEL,
        tools=tools,
        system_prompt=build_system_prompt(tool_names(tools)),
        retry_policy=RetryPolicy(max_attempts=1),
    )


@needs_ollama
def test_generate_uses_tool_and_answers():
    gateway = _gateway()
    conv = Conversation(id="t", turns=[Turn(role=USER, content="What is 6 * 7? Use the calculate tool.")])
    turn = gateway.generate(conv)
    assert "42" in turn.content, turn.content
    assert "calculate" in turn.tool_uses, turn.tool_uses


@needs_ollama
def test_stream_yields_text_and_tool_chunks():
    async def collect():
        gateway = _gateway()
        conv = Conversation(id="t", turns=[Turn(role=USER, content="What is 6 * 7? Use the calculate tool.")])
        return [c async for c in gateway.stream(conv)]

    chunks = asyncio.run(collect())
    kinds = {c.kind for c in chunks}
    assert "text" in kinds
    assert "tool" in kinds
    assert any(c.payload == "calculate" for c in chunks if c.kind == "tool")
    assert "42" in "".join(c.payload for c in chunks if c.kind == "text")
