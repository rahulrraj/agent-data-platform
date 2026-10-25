"""Controller boundary tests (§9): parsing, validation, response and error mapping.

The service is faked — no model involved.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_api.controllers.http import create_app
from agent_api.services.ask_agent import (
    AgentReply,
    AgentServiceError,
    AskAgentService,
    InvalidMessageError,
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
        from agent_api.services.ask_agent import ReadinessReport

        if self.error:
            return ReadinessReport(ready=False, failing=("model: down",))
        return ReadinessReport(ready=True, failing=())


def make_client(**kwargs) -> TestClient:
    return TestClient(create_app(FakeService(**kwargs), model_label="test:model"))


def test_health():
    r = make_client().get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "model": "test:model"}


def test_ask_ok():
    r = make_client().post("/api/ask", json={"message": "hi", "session_id": "s"})
    assert r.status_code == 200
    assert r.json() == {"reply": "hello back", "tools_used": ["calculate"], "session_id": "s"}


def test_ask_blank_message_is_400():
    r = make_client().post("/api/ask", json={"message": "  ", "session_id": "s"})
    assert r.status_code == 400


def test_ask_service_failure_is_502():
    r = make_client(error=AgentServiceError("model exploded")).post(
        "/api/ask", json={"message": "hi", "session_id": "s"}
    )
    assert r.status_code == 502


def test_chat_streams_sse_and_done():
    r = make_client().post("/api/chat", json={"message": "hi", "session_id": "s"})
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    body = r.text
    assert '"type": "text"' in body
    assert '"type": "tool"' in body
    assert body.rstrip().endswith("data: [DONE]")


def test_chat_blank_message_yields_error_event():
    r = make_client().post("/api/chat", json={"message": "  ", "session_id": "s"})
    assert r.status_code == 200  # SSE carries the error as an event
    assert '"type": "error"' in r.text


def test_ready_ok():
    r = make_client().get("/api/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready"}


def test_ready_not_ready_is_503():
    r = make_client(error=AgentServiceError("model exploded")).get("/api/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["failing"] == ["model: down"]


# --- M-18 / M-6 / C-1 / M-17 additions ---

import asyncio


def test_ask_endpoint_is_sync_not_coroutine():
    # M-18: /api/ask must run in FastAPI's threadpool, not on the loop.
    app = create_app(FakeService(), model_label="test:model")
    route = next(r for r in app.routes if getattr(r, "path", None) == "/api/ask")
    assert not asyncio.iscoroutinefunction(route.endpoint)


def test_missing_session_id_is_422():
    # M-6: no "default" fallback session — clients must pass an id.
    r = make_client().post("/api/ask", json={"message": "hi"})
    assert r.status_code == 422
    r = make_client().post("/api/chat", json={"message": "hi"})
    assert r.status_code == 422


def test_cors_default_is_same_origin_only():
    # C-1: with no CORS_ORIGINS configured, a foreign Origin gets no
    # Access-Control-Allow-Origin header back.
    r = make_client().get(
        "/api/health", headers={"Origin": "https://evil.example"}
    )
    assert "access-control-allow-origin" not in r.headers


def test_cors_configured_origin_is_allowed():
    app = create_app(
        FakeService(), model_label="test:model",
        cors_origins=("https://ui.example",),
    )
    from fastapi.testclient import TestClient as _TC

    r = _TC(app).get("/api/health", headers={"Origin": "https://ui.example"})
    assert r.headers.get("access-control-allow-origin") == "https://ui.example"


class _ResetService(FakeService):
    def __init__(self):
        super().__init__()
        self.reset_calls = []

    def reset_session(self, conversation_id: str) -> None:
        self.reset_calls.append(conversation_id)


def test_reset_session_endpoint():
    service = _ResetService()
    from fastapi.testclient import TestClient as _TC

    client = _TC(create_app(service, model_label="test:model"))
    r = client.delete("/api/sessions/s1")
    assert r.status_code == 200
    assert r.json() == {"status": "reset", "session_id": "s1"}
    assert service.reset_calls == ["s1"]
