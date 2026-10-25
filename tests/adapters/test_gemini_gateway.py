"""Gemini gateway unit tests: transient classification must match what the
SDKs actually raise (C-2) — no live Gemini calls.

Strands converts 429/503 to ModelThrottledException; the google-genai
client raises APIError subclasses with a numeric `code`.
langchain_google_genai converts 429 -> GoogleRateLimitError and 5xx ->
GoogleAPIError. The old google.api_core check matched none of these (and
google.api_core is not installed), so 429/503 were never retried.
Mirrors tests/adapters/test_bedrock_gateway.py:97-110.
"""

from __future__ import annotations

import pytest
from google.genai.errors import ClientError as GenAIClientError
from google.genai.errors import ServerError as GenAIServerError
from langchain_google_genai.chat_models import GoogleAPIError, GoogleRateLimitError
from strands.types.exceptions import ModelThrottledException

from agent_api.adapters.gemini_model_gateway import GeminiModelGateway
from agent_api.adapters.langgraph_gemini_gateway import LangGraphGeminiGateway
from agent_api.domain.conversation import USER, Conversation, Turn
from agent_api.platform.retry import RetryPolicy
from agent_api.ports.model_gateway import ModelGatewayError


def _strands_gateway(**overrides):
    kwargs = dict(
        api_key="test-key",
        model_id="gemini-flash-latest",
        tools=[],
        system_prompt="test",
        retry_policy=RetryPolicy(max_attempts=1, base_delay=0),
    )
    kwargs.update(overrides)
    return GeminiModelGateway(**kwargs)


def _langgraph_gateway(**overrides):
    kwargs = dict(
        api_key="test-key",
        model_id="gemini-flash-latest",
        tools=[],
        system_prompt="test",
        retry_policy=RetryPolicy(max_attempts=1, base_delay=0),
    )
    kwargs.update(overrides)
    return LangGraphGeminiGateway(**kwargs)


def _conv():
    return Conversation(id="t", turns=[Turn(role=USER, content="hi")])


class _FakeResult:
    def __init__(self, text="42"):
        self.message = {"content": [{"text": text}]}


class _FakeAgent:
    def __init__(self, fail_with=None):
        self.messages = []
        self._fail_with = fail_with
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self._fail_with is not None:
            raise self._fail_with
        self.messages.append({"content": [{"toolUse": {"name": "calculate"}}]})
        return _FakeResult()


class _FakeGraphAgent:
    def __init__(self, fail_with=None):
        self._fail_with = fail_with
        self.calls = 0

    def invoke(self, state):
        self.calls += 1
        if self._fail_with is not None:
            raise self._fail_with
        return {"messages": []}


def test_strands_is_transient_classification():
    gateway = _strands_gateway()
    assert gateway._is_transient(ModelThrottledException("throttled"))
    assert gateway._is_transient(GenAIServerError(503, "unavailable"))
    assert gateway._is_transient(GenAIServerError(500, "internal"))
    assert gateway._is_transient(GenAIClientError(429, "rate limited"))
    assert not gateway._is_transient(GenAIClientError(400, "bad request"))
    assert not gateway._is_transient(GenAIClientError(401, "unauthorized"))
    assert not gateway._is_transient(ValueError("plain bug"))


def test_langgraph_is_transient_classification():
    gateway = _langgraph_gateway()
    assert gateway._is_transient(GoogleRateLimitError("rate limited"))
    assert gateway._is_transient(GoogleAPIError(503, "unavailable"))
    assert gateway._is_transient(GenAIServerError(503, "unavailable"))
    assert gateway._is_transient(GenAIClientError(429, "rate limited"))
    assert not gateway._is_transient(GenAIClientError(400, "bad request"))
    assert not gateway._is_transient(ValueError("plain bug"))


def test_strands_generate_retries_transient():
    gateway = _strands_gateway(retry_policy=RetryPolicy(max_attempts=3, base_delay=0))
    agent = _FakeAgent(fail_with=GenAIServerError(503, "unavailable"))
    gateway._build_agent = lambda conv: agent
    with pytest.raises(ModelGatewayError, match="gemini"):
        gateway.generate(_conv())
    assert agent.calls == 3


def test_langgraph_generate_retries_transient():
    gateway = _langgraph_gateway(retry_policy=RetryPolicy(max_attempts=3, base_delay=0))
    agent = _FakeGraphAgent(fail_with=GoogleRateLimitError("rate limited"))
    gateway._build_agent = lambda: agent
    with pytest.raises(ModelGatewayError, match="langgraph/gemini"):
        gateway.generate(_conv())
    assert agent.calls == 3


def test_strands_generate_happy_path():
    gateway = _strands_gateway()
    gateway._build_agent = lambda conv: _FakeAgent()
    turn = gateway.generate(_conv())
    assert turn.content == "42"
    assert turn.tool_uses == ("calculate",)
