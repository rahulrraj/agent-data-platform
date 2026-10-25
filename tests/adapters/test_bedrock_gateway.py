"""BedrockModelGateway unit tests (mocked boto3 / BedrockModel, no live AWS)."""

from __future__ import annotations

import asyncio
import json

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from agent_api.adapters.bedrock_model_gateway import BedrockModelGateway
from agent_api.domain.conversation import USER, Conversation, Turn
from agent_api.platform.retry import RetryPolicy
from agent_api.ports.model_gateway import ModelGatewayError


def _gateway(**overrides):
    kwargs = dict(
        region_name="us-east-1",
        model_id="us.anthropic.claude-sonnet-5-5",
        tools=[],
        system_prompt="test",
        retry_policy=RetryPolicy(max_attempts=1, base_delay=0),
    )
    kwargs.update(overrides)
    return BedrockModelGateway(**kwargs)


def _conv():
    return Conversation(id="t", turns=[Turn(role=USER, content="hi")])


class _FakeResult:
    def __init__(self, text="42"):
        self.message = {"content": [{"text": text}]}


class _FakeAgent:
    def __init__(self, fail_with=None):
        # Messages start empty (seeded history) and the turn appends its
        # own toolUse message on __call__, like a real Strands agent:
        # _generate_once attributes only post-seed messages to the turn.
        self.messages = []
        self._fail_with = fail_with
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self._fail_with is not None:
            raise self._fail_with
        self.messages.append({"content": [{"toolUse": {"name": "calculate"}}]})
        return _FakeResult()

    async def stream_async(self):
        yield {"data": "4"}
        yield {"data": "2"}
        yield {"current_tool_use": {"name": "calculate"}}


def test_build_model_passes_region_and_model(monkeypatch):
    seen = {}

    class CapturingBedrockModel:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr("strands.models.BedrockModel", CapturingBedrockModel)
    _gateway()._build_model()
    assert seen["region_name"] == "us-east-1"
    assert seen["model_id"] == "us.anthropic.claude-sonnet-5-5"
    # m-10: read timeout pinned alongside region/model.
    assert seen["boto_client_config"].read_timeout == 120


def test_generate_happy_path(monkeypatch):
    gateway = _gateway()
    gateway._build_agent = lambda conv: _FakeAgent()
    turn = gateway.generate(_conv())
    assert turn.content == "42"
    assert turn.tool_uses == ("calculate",)


def test_stream_happy_path(monkeypatch):
    gateway = _gateway()
    gateway._build_agent = lambda conv: _FakeAgent()
    chunks = asyncio.run(_collect(gateway))
    kinds = [c.kind for c in chunks]
    assert kinds == ["text", "text", "tool"]
    assert "".join(c.payload for c in chunks if c.kind == "text") == "42"
    assert [c.payload for c in chunks if c.kind == "tool"] == ["calculate"]


async def _collect(gateway):
    return [c async for c in gateway.stream(_conv())]


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": "m"}}, "InvokeModel")


def test_is_transient_classification():
    gateway = _gateway()
    assert gateway._is_transient(_client_error("ThrottlingException"))
    assert gateway._is_transient(_client_error("ServiceUnavailableException"))
    assert gateway._is_transient(_client_error("ModelNotReadyException"))
    assert gateway._is_transient(EndpointConnectionError(endpoint_url="https://x"))
    assert not gateway._is_transient(_client_error("AccessDeniedException"))
    assert not gateway._is_transient(ValueError("plain bug"))


def test_is_transient_strands_throttling():
    from strands.types.exceptions import ModelThrottledException

    assert _gateway()._is_transient(ModelThrottledException("throttled"))


def test_generate_translates_errors(monkeypatch):
    gateway = _gateway()
    gateway._build_agent = lambda conv: _FakeAgent(
        fail_with=_client_error("ThrottlingException")
    )
    with pytest.raises(ModelGatewayError, match="bedrock"):
        gateway.generate(_conv())


def test_generate_retries_transient(monkeypatch):
    gateway = _gateway(retry_policy=RetryPolicy(max_attempts=3, base_delay=0))
    agent = _FakeAgent(fail_with=_client_error("ThrottlingException"))
    gateway._build_agent = lambda conv: agent
    with pytest.raises(ModelGatewayError, match="bedrock"):
        gateway.generate(_conv())
    assert agent.calls == 3


def test_ping_requires_credentials(monkeypatch):
    class FakeSession:
        def __init__(self, creds):
            self._creds = creds

        def get_credentials(self):
            return self._creds

    gateway = _gateway()
    monkeypatch.setattr("boto3.Session", lambda **kw: FakeSession(None))
    with pytest.raises(ModelGatewayError, match="no AWS credentials"):
        gateway.ping()
    monkeypatch.setattr("boto3.Session", lambda **kw: FakeSession(object()))
    gateway.ping()  # no raise


# --- m-10 timeout pin (2026-10-25) ---

def test_bedrock_read_timeout_pinned(monkeypatch):
    captured = {}

    class _FakeBedrockModel:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("strands.models.BedrockModel", _FakeBedrockModel)
    from agent_api.adapters.bedrock_model_gateway import BedrockModelGateway

    gateway = BedrockModelGateway(
        region_name="us-east-1", model_id="m", tools=[], system_prompt="s",
        retry_policy=RetryPolicy(),
    )
    gateway._build_model()
    assert captured["boto_client_config"].read_timeout == 120
