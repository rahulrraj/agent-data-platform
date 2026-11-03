"""Inbound HTTP adapter (controller, §2).

Parses and validates transport input, invokes the use case, and maps
application errors to HTTP status codes (§5). Knows nothing about model
providers — it only sees the AskAgentService.

Session ids: API clients must pass an explicit `session_id` on every
call — there is no "default" fallback session shared by anonymous
callers.

Authentication (ADR 0008): when a token validator is wired (the
composition root wires one whenever AUTH_ISSUER is configured), every
data endpoint requires `Authorization: Bearer <token>`, validated
before any service work — including `/api/chat`, where validation
happens before the streaming response is returned, so an
unauthenticated stream never starts. The session-isolation gap is
closed when auth is on: the controller extracts the principal and
passes it (with the raw token, for downstream forwarding) to the
service, which binds each session to its first writer and rejects a
different principal with 403. `/api/health` and `/api/ready` stay
public (ops endpoints, no data). When no validator is wired
(AUTH_DISABLED=true, local development only), auth is off entirely
and anyone who can reach the API can read or append any session id
they know — the pre-auth behavior, kept only behind the explicit
dev opt-out.
"""

from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from agent_api.ports.token_validator import (
    TokenValidationError,
    TokenValidator,
    TokenValidatorUnavailableError,
)
from agent_api.services.ask_agent import (
    AgentServiceError,
    AskAgentError,
    AskAgentService,
    ForbiddenError,
    InvalidMessageError,
)


class ChatRequest(BaseModel):
    message: str
    # Required: no default. The UI always generates and sends one; direct
    # API clients must do the same (missing -> 422 from pydantic).
    session_id: str
    # Optional allowlist key selecting the answering model (ADR 0007).
    # Absent (or "default") means the configured provider + model.
    # Unknown keys are a 400; embeddings are never dynamic — only the
    # answering model switches. Arbitrary provider/model pairs are
    # never accepted: only server-configured keys exist.
    model: str | None = None


class ChatResponse(BaseModel):
    reply: str
    tools_used: list[str]
    session_id: str


def _error_status(exc: AskAgentError) -> int:
    if isinstance(exc, InvalidMessageError):
        return 400
    if isinstance(exc, ForbiddenError):
        return 403
    return 502


def _to_http_error(exc: AskAgentError) -> HTTPException:
    return HTTPException(status_code=_error_status(exc), detail=str(exc))


def _authenticate(
    request: Request, validator: TokenValidator | None
) -> tuple[str | None, str | None]:
    """Extract and validate the bearer token; (principal, raw token).

    No validator means auth is off (dev opt-out): returns (None, None)
    and the service enforces nothing. Error details are generic on
    purpose — they never include token material or validator internals.
    """
    if validator is None:
        return None, None
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="authentication required")
    token = token.strip()
    try:
        principal = validator.validate(token)
    except TokenValidatorUnavailableError:
        raise HTTPException(
            status_code=503, detail="authentication service unavailable"
        ) from None
    except TokenValidationError:
        raise HTTPException(
            status_code=401, detail="invalid or expired token"
        ) from None
    return principal, token


def create_app(
    service: AskAgentService,
    model_label: str,
    cors_origins: tuple[str, ...] | list[str] = (),
    model_keys: tuple[str, ...] = ("default",),
    token_validator: TokenValidator | None = None,
) -> FastAPI:
    """Builds the FastAPI app with the use case injected.

    `cors_origins` comes from Settings (CORS_ORIGINS, comma-separated);
    empty means same-origin only — cross-origin browser access is
    opt-in per deployment, never "*" by default. `model_keys` lists the
    caller-facing model keys from the composition root's allowlist
    (ADR 0007); the health payload reports them additively.
    `token_validator`, when wired, gates every data endpoint on a
    valid bearer token (ADR 0008); None leaves auth off (dev only).
    """
    app = FastAPI(title="Agent API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "model": model_label,
            "models": list(model_keys),
            "default_model": "default",
        }

    @app.get("/api/ready")
    def ready():
        report = service.readiness()
        if report.ready:
            return {"status": "ready"}
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "failing": list(report.failing)},
        )

    @app.post("/api/ask", response_model=ChatResponse)
    def ask(req: ChatRequest, request: Request):
        # Sync def, deliberately: FastAPI runs sync endpoints in its
        # threadpool, so a minutes-long model turn cannot block the
        # event loop (the old `async def` awaited nothing and stalled
        # /api/health, /api/ready and /api/chat for the whole turn).
        principal, user_token = _authenticate(request, token_validator)
        try:
            reply = service.ask(
                req.session_id,
                req.message,
                model=req.model,
                principal=principal,
                user_token=user_token,
            )
        except AskAgentError as exc:
            raise _to_http_error(exc) from exc
        return ChatResponse(
            reply=reply.text, tools_used=list(reply.tool_uses), session_id=req.session_id
        )

    @app.post("/api/chat")
    async def chat(req: ChatRequest, request: Request):
        # Authenticate BEFORE the StreamingResponse is returned: an
        # unauthenticated caller gets a plain 401, never a started
        # stream. Token expiry mid-stream is validated at turn start
        # only (ADR 0008); the turn ends and the UI re-authenticates.
        principal, user_token = _authenticate(request, token_validator)

        async def event_stream():
            try:
                async for chunk in service.ask_stream(
                    req.session_id,
                    req.message,
                    model=req.model,
                    principal=principal,
                    user_token=user_token,
                ):
                    yield f"data: {json.dumps({'type': chunk.kind, 'payload': chunk.payload})}\n\n"
            except AskAgentError as exc:
                status = _error_status(exc)
                yield f"data: {json.dumps({'type': 'error', 'status': status, 'error': str(exc)})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.delete("/api/sessions/{session_id}")
    def reset_session(session_id: str, request: Request):
        """Clear a session's history (fresh start for a bricked session)."""
        principal, _ = _authenticate(request, token_validator)
        try:
            service.reset_session(session_id, principal=principal)
        except AskAgentError as exc:
            raise _to_http_error(exc) from exc
        return {"status": "reset", "session_id": session_id}

    return app
