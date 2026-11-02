"""BedrockEmbeddingGateway unit tests (mocked boto3, no live AWS)."""

from __future__ import annotations

import json

import pytest

from agent_api.adapters.bedrock_embedding_gateway import BedrockEmbeddingGateway
from agent_api.ports.embedding_gateway import EmbeddingGatewayError


class _FakeBody:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode()

    def read(self):
        return self._data


class _FakeBedrockClient:
    def __init__(self):
        self.calls = []

    def invoke_model(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "body": _FakeBody(
                {"embedding": [0.1, 0.2, 0.3], "inputTextTokenCount": 3}
            )
        }


def _gateway_with_fake_client(monkeypatch):
    client = _FakeBedrockClient()
    monkeypatch.setattr("boto3.client", lambda *a, **kw: client)
    return BedrockEmbeddingGateway("us-east-1"), client


def test_model_name_default():
    assert (
        BedrockEmbeddingGateway("us-east-1").model_name
        == "amazon.titan-embed-text-v2:0"
    )


def test_similarity_threshold_titan_v2_is_provisional_0_30():
    # Provisional: a single observed true question scoring 0.30, no
    # margin and no false-positive calibration. See the comment at the
    # value in the adapter; a Titan calibration run is still needed.
    assert BedrockEmbeddingGateway("us-east-1").similarity_threshold == 0.30


def test_similarity_threshold_other_model_uses_default():
    assert (
        BedrockEmbeddingGateway(
            "us-east-1", model_id="amazon.titan-embed-text-v1"
        ).similarity_threshold
        == 0.60
    )


def test_embed_invokes_once_per_text(monkeypatch):
    gateway, client = _gateway_with_fake_client(monkeypatch)
    vectors = gateway.embed(["hello", "world"])
    assert vectors == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert len(client.calls) == 2
    assert client.calls[0]["modelId"] == "amazon.titan-embed-text-v2:0"
    body0 = json.loads(client.calls[0]["body"])
    assert body0["inputText"] == "hello"
    assert body0["dimensions"] == 1024
    assert body0["normalize"] is True
    body1 = json.loads(client.calls[1]["body"])
    assert body1["inputText"] == "world"


def test_ping_requires_credentials(monkeypatch):
    class FakeSession:
        def __init__(self, creds):
            self._creds = creds

        def get_credentials(self):
            return self._creds

    gateway = BedrockEmbeddingGateway("us-east-1")
    monkeypatch.setattr("boto3.Session", lambda **kw: FakeSession(None))
    with pytest.raises(EmbeddingGatewayError, match="no AWS credentials"):
        gateway.ping()
    monkeypatch.setattr("boto3.Session", lambda **kw: FakeSession(object()))
    gateway.ping()  # no raise
