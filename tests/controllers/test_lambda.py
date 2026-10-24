"""Lambda controller boundary tests (§9): event translation, routing,
response shapes, error mapping, and the buffered chat body.

Mangum is an optional (lambda) extra: skip the whole file when it is not
installed. The service is faked — no model involved. The Lambda handler
is exercised end to end through Mangum with crafted payload-v2 events
(Function URL / HTTP API shape).
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("mangum")

from agent_api.controllers import lambda_handler
from agent_api.controllers.http import create_app
from agent_api.services.ask_agent import (
    AgentReply,
    AgentServiceError,
    AskAgentService,
    InvalidMessageError,
    ReadinessReport,
)


class FakeService(AskAgentService):
    def __init__(self, reply: AgentReply | None = None, error: Exception | None = None):
        self.reply = reply or AgentReply(text="hello back", tool_uses=("calculate",))
        self.error = error

    def ask(self, conversation_id: str, message: str) -> AgentReply:
        if self.error:
            raise self.error
        if not message.strip():
            raise InvalidMessageError("message is required")
        return self.reply

    async def ask_stream(self, conversation_id: str, message: str):
        if self.error:
            raise self.error
        if not message.strip():
            raise InvalidMessageError("message is required")
        from agent_api.ports.model_gateway import StreamChunk

        yield StreamChunk(kind="text", payload=self.reply.text)
        for tool in self.reply.tool_uses:
            yield StreamChunk(kind="tool", payload=tool)

    def readiness(self):
        if self.error:
            return ReadinessReport(ready=False, failing=("model: down",))
        return ReadinessReport(ready=True, failing=())


class FakeContext:
    """Minimal stand-in for the Lambda context object (Mangum ignores it)."""

    function_name = "test-function"
    function_version = "$LATEST"
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:test-function"
    memory_limit_in_mb = 512
    aws_request_id = "test-request-id"
    log_group_name = "/aws/lambda/test-function"
    log_stream_name = "test-stream"


def v2_event(method: str, path: str, body: dict | None = None) -> dict:
    """Payload version 2.0 event (Function URL / HTTP API shape)."""
    return {
        "version": "2.0",
        "routeKey": f"{method} {path}",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"content-type": "application/json"},
        "queryStringParameters": None,
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "test",
            "http": {
                "method": method,
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "127.0.0.1",
                "userAgent": "test",
            },
            "requestId": "test-request-id",
        },
        "body": json.dumps(body) if body is not None else "",
        "isBase64Encoded": False,
    }


def call_lambda(method: str, path: str, body: dict | None = None, **service_kwargs) -> dict:
    """Invoke the handler with the test app patched in (fresh per call)."""
    app = create_app(FakeService(**service_kwargs), model_label="test:model")
    lambda_handler.build_app = lambda: app  # noqa: ARG001 — handler builds with no args
    lambda_handler._mangum = None
    try:
        return lambda_handler.handler(v2_event(method, path, body), FakeContext())
    finally:
        lambda_handler._mangum = None


def test_health():
    r = call_lambda("GET", "/api/health")
    assert r["statusCode"] == 200
    assert json.loads(r["body"]) == {"status": "ok", "model": "test:model"}


def test_ask_ok():
    r = call_lambda("POST", "/api/ask", {"message": "hi", "session_id": "s"})
    assert r["statusCode"] == 200
    assert json.loads(r["body"]) == {
        "reply": "hello back",
        "tools_used": ["calculate"],
        "session_id": "s",
    }


def test_ask_blank_message_is_400():
    r = call_lambda("POST", "/api/ask", {"message": "  ", "session_id": "s"})
    assert r["statusCode"] == 400


def test_ask_service_failure_is_502():
    r = call_lambda(
        "POST",
        "/api/ask",
        {"message": "hi", "session_id": "s"},
        error=AgentServiceError("model exploded"),
    )
    assert r["statusCode"] == 502


def test_ready_ok():
    r = call_lambda("GET", "/api/ready")
    assert r["statusCode"] == 200
    assert json.loads(r["body"]) == {"status": "ready"}


def test_ready_not_ready_is_503():
    r = call_lambda("GET", "/api/ready", error=AgentServiceError("model exploded"))
    assert r["statusCode"] == 503
    body = json.loads(r["body"])
    assert body["status"] == "not_ready"
    assert body["failing"] == ["model: down"]


def test_chat_returns_buffered_sse_with_done():
    # Mangum buffers the whole ASGI body: the chat reply arrives as one
    # complete SSE document, not incrementally. The body must still be
    # valid SSE ending in [DONE].
    r = call_lambda("POST", "/api/chat", {"message": "hi", "session_id": "s"})
    assert r["statusCode"] == 200
    content_type = next(
        v for k, v in r["headers"].items() if k.lower() == "content-type"
    )
    assert "text/event-stream" in content_type
    assert '"type": "text"' in r["body"]
    assert '"type": "tool"' in r["body"]
    assert r["body"].rstrip().endswith("data: [DONE]")


def test_chat_blank_message_yields_error_event():
    r = call_lambda("POST", "/api/chat", {"message": "  ", "session_id": "s"})
    assert r["statusCode"] == 200  # SSE carries the error as an event
    assert '"type": "error"' in r["body"]


def test_unknown_route_is_404():
    r = call_lambda("GET", "/api/nope")
    assert r["statusCode"] == 404


def test_wrong_method_is_405():
    r = call_lambda("GET", "/api/ask")
    assert r["statusCode"] == 405
