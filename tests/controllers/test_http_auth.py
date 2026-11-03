"""Controller auth tests (ADR 0008): 401/403/503 mapping, pre-stream
auth on /api/chat, public health/ready, principal + token handoff.

The service and validator are faked — no model, no network.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_api.controllers.http import create_app
from agent_api.ports.token_validator import (
    TokenValidationError,
    TokenValidatorUnavailableError,
)
from agent_api.services.ask_agent import (
    AgentReply,
    AskAgentService,
    ForbiddenError,
)

TOKEN = "test-token-abc"
PRINCIPAL = "user-123"


class FakeValidator:
    """TokenValidator stub: one canned outcome per test."""

    def __init__(self, principal: str | None = PRINCIPAL,
                 error: Exception | None = None):
        self.principal = principal
        self.error = error
        self.seen_tokens: list[str] = []

    def validate(self, token: str) -> str:
        self.seen_tokens.append(token)
        if self.error is not None:
            raise self.error
        assert self.principal is not None
        return self.principal


class FakeService(AskAgentService):
    def __init__(self, error: Exception | None = None):
        self.reply = AgentReply(text="hello back", tool_uses=("calculate",))
        self.error = error
        self.calls: list[tuple] = []

    def ask(self, conversation_id, message, model=None,
            principal=None, user_token=None):
        self.calls.append(("ask", conversation_id, principal, user_token))
        if self.error:
            raise self.error
        return self.reply

    async def ask_stream(self, conversation_id, message, model=None,
                         principal=None, user_token=None):
        self.calls.append(("chat", conversation_id, principal, user_token))
        if self.error:
            raise self.error
        from agent_api.ports.model_gateway import StreamChunk

        yield StreamChunk(kind="text", payload=self.reply.text)

    def reset_session(self, conversation_id, principal=None):
        self.calls.append(("reset", conversation_id, principal))
        if self.error:
            raise self.error

    def readiness(self):
        from agent_api.services.ask_agent import ReadinessReport

        return ReadinessReport(ready=True, failing=())


def _client(service=None, validator=None) -> TestClient:
    return TestClient(
        create_app(
            service or FakeService(),
            model_label="test:model",
            token_validator=validator,
        )
    )


def _authed_client(service=None, validator=None) -> TestClient:
    return _client(service, validator or FakeValidator())


def test_missing_token_is_401_and_service_not_called():
    service = FakeService()
    r = _client(service, FakeValidator()).post(
        "/api/ask", json={"message": "hi", "session_id": "s"}
    )
    assert r.status_code == 401
    assert service.calls == []


def test_malformed_authorization_header_is_401():
    service = FakeService()
    client = _client(service, FakeValidator())
    for header in ("Token abc", "Bearer", "Bearer ", "Basic dXNlcjpwYXNz"):
        r = client.post(
            "/api/ask",
            json={"message": "hi", "session_id": "s"},
            headers={"Authorization": header},
        )
        assert r.status_code == 401
    assert service.calls == []


def test_invalid_token_is_401_with_generic_detail():
    service = FakeService()
    r = _client(service, FakeValidator(error=TokenValidationError("bad sig"))).post(
        "/api/ask",
        json={"message": "hi", "session_id": "s"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid or expired token"
    # No token material or validator internals in the response.
    assert TOKEN not in r.text
    assert "bad sig" not in r.text
    assert service.calls == []


def test_unavailable_validator_is_503():
    service = FakeService()
    r = _client(
        service, FakeValidator(error=TokenValidatorUnavailableError("down"))
    ).post(
        "/api/ask",
        json={"message": "hi", "session_id": "s"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert r.status_code == 503
    assert r.json()["detail"] == "authentication service unavailable"
    assert service.calls == []


def test_valid_token_passes_principal_and_token_to_service():
    service = FakeService()
    r = _authed_client(service).post(
        "/api/ask",
        json={"message": "hi", "session_id": "s"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert r.status_code == 200
    assert service.calls == [("ask", "s", PRINCIPAL, TOKEN)]


def test_forbidden_session_is_403():
    service = FakeService(error=ForbiddenError("session belongs to a different user"))
    r = _authed_client(service).post(
        "/api/ask",
        json={"message": "hi", "session_id": "s"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert r.status_code == 403


def test_health_and_ready_stay_public_with_validator_wired():
    client = _client(FakeService(), FakeValidator())
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/ready").status_code == 200


def test_chat_auth_checked_before_stream_starts():
    service = FakeService()
    r = _client(service, FakeValidator()).post(
        "/api/chat", json={"message": "hi", "session_id": "s"}
    )
    assert r.status_code == 401
    assert "text/event-stream" not in r.headers.get("content-type", "")
    assert "[DONE]" not in r.text
    assert service.calls == []


def test_chat_invalid_token_is_401_not_an_sse_error_event():
    service = FakeService()
    r = _client(service, FakeValidator(error=TokenValidationError("expired"))).post(
        "/api/chat",
        json={"message": "hi", "session_id": "s"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert r.status_code == 401
    assert service.calls == []


def test_chat_valid_token_streams_and_passes_identity():
    service = FakeService()
    r = _authed_client(service).post(
        "/api/chat",
        json={"message": "hi", "session_id": "s"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert r.status_code == 200
    assert '"type": "text"' in r.text
    assert service.calls == [("chat", "s", PRINCIPAL, TOKEN)]


def test_chat_forbidden_session_yields_403_error_event():
    service = FakeService(error=ForbiddenError("session belongs to a different user"))
    r = _authed_client(service).post(
        "/api/chat",
        json={"message": "hi", "session_id": "s"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert r.status_code == 200  # SSE carries the error as an event
    assert '"type": "error"' in r.text
    assert '"status": 403' in r.text


def test_reset_requires_auth():
    service = FakeService()
    r = _client(service, FakeValidator()).delete("/api/sessions/s1")
    assert r.status_code == 401
    assert service.calls == []


def test_reset_passes_principal_and_forbidden_is_403():
    service = FakeService()
    client = _authed_client(service)
    r = client.delete(
        "/api/sessions/s1", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert r.status_code == 200
    assert service.calls == [("reset", "s1", PRINCIPAL)]

    forbidden = FakeService(error=ForbiddenError("session belongs to a different user"))
    r = _authed_client(forbidden).delete(
        "/api/sessions/s1", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert r.status_code == 403


def test_no_validator_means_no_auth_and_no_identity():
    # Dev opt-out: the controller passes (None, None), as before.
    service = FakeService()
    r = _client(service, None).post(
        "/api/ask", json={"message": "hi", "session_id": "s"}
    )
    assert r.status_code == 200
    assert service.calls == [("ask", "s", None, None)]


def test_bearer_scheme_is_case_insensitive():
    service = FakeService()
    r = _authed_client(service).post(
        "/api/ask",
        json={"message": "hi", "session_id": "s"},
        headers={"Authorization": f"bearer {TOKEN}"},
    )
    assert r.status_code == 200
