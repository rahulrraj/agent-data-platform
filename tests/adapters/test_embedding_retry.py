"""Embedding gateway retries (M-4): transient failures retry, others don't,
and failures surface as the typed port error."""

from __future__ import annotations

import json

import httpx
import pytest
from botocore.exceptions import ClientError as BotoClientError
from google.genai.errors import ClientError as GenAIClientError
from google.genai.errors import ServerError as GenAIServerError

from agent_api.adapters.bedrock_embedding_gateway import BedrockEmbeddingGateway
from agent_api.adapters.gemini_embedding_gateway import GeminiEmbeddingGateway
from agent_api.adapters.ollama_embedding_gateway import OllamaEmbeddingGateway
from agent_api.platform.retry import RetryPolicy
from agent_api.ports.embedding_gateway import EmbeddingGatewayError


def _retry3():
    return RetryPolicy(max_attempts=3, base_delay=0)


# --- Gemini ---

class _FakeGenAIEmbeddings:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeGenAIModels:
    def __init__(self, embed_fn):
        self.embed_content = embed_fn


class _FakeGenAIClient:
    def __init__(self, embed_fn):
        self.models = _FakeGenAIModels(embed_fn)


class _FakeEmbedding:
    def __init__(self, values):
        self.values = values


class _FakeEmbedResult:
    def __init__(self, vectors):
        self.embeddings = [_FakeEmbedding(v) for v in vectors]


def test_gemini_embed_retries_503(monkeypatch):
    embed_fn = _FakeGenAIEmbeddings(
        [
            GenAIServerError(503, "unavailable"),
            GenAIServerError(503, "unavailable"),
            _FakeEmbedResult([[0.1, 0.2]]),
        ]
    )
    monkeypatch.setattr(
        "google.genai.Client", lambda **kw: _FakeGenAIClient(embed_fn)
    )
    gateway = GeminiEmbeddingGateway("key", retry_policy=_retry3())
    assert gateway.embed(["hi"]) == [[0.1, 0.2]]
    assert embed_fn.calls == 3


def test_gemini_embed_400_not_retried(monkeypatch):
    embed_fn = _FakeGenAIEmbeddings([GenAIClientError(400, "bad request")])
    monkeypatch.setattr(
        "google.genai.Client", lambda **kw: _FakeGenAIClient(embed_fn)
    )
    gateway = GeminiEmbeddingGateway("key", retry_policy=_retry3())
    with pytest.raises(EmbeddingGatewayError):
        gateway.embed(["hi"])
    assert embed_fn.calls == 1


def test_gemini_ping_is_typed_error():
    with pytest.raises(EmbeddingGatewayError, match="GEMINI_API_KEY"):
        GeminiEmbeddingGateway("").ping()


# --- Ollama ---

class _OllamaStubClient:
    def __init__(self, statuses):
        self._statuses = list(statuses)
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, json=None):
        self.calls += 1
        status = self._statuses.pop(0)
        if status == 200:
            return httpx.Response(
                200,
                json={"embeddings": [[0.5]]},
                request=httpx.Request("POST", url),
            )
        return httpx.Response(
            status, request=httpx.Request("POST", url)
        )


def test_ollama_embed_retries_500(monkeypatch):
    client = _OllamaStubClient([500, 500, 200])
    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: client)
    gateway = OllamaEmbeddingGateway("http://localhost:11434", retry_policy=_retry3())
    assert gateway.embed(["hi"]) == [[0.5]]
    assert client.calls == 3


# --- Bedrock ---

class _FakeBody:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode()

    def read(self):
        return self._data


class _FlakyBedrockClient:
    def __init__(self, failures):
        self._failures = failures
        self.calls = 0

    def invoke_model(self, **kwargs):
        self.calls += 1
        if self.calls <= self._failures:
            raise BotoClientError(
                {"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
                "InvokeModel",
            )
        return {"body": _FakeBody({"embedding": [0.1, 0.2]})}


def test_bedrock_embed_retries_throttling(monkeypatch):
    client = _FlakyBedrockClient(failures=2)
    monkeypatch.setattr("boto3.client", lambda *a, **kw: client)
    gateway = BedrockEmbeddingGateway("us-east-1", retry_policy=_retry3())
    assert gateway.embed(["hi"]) == [[0.1, 0.2]]
    assert client.calls == 3
