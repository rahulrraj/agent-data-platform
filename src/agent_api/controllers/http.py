"""Inbound HTTP adapter (controller, §2).

Parses and validates transport input, invokes the use case, and maps
application errors to HTTP status codes (§5). Knows nothing about model
providers — it only sees the AskAgentService.

Session ids: API clients must pass an explicit `session_id` on every
call — there is no "default" fallback session shared by anonymous
callers. (Binding a session id to an authenticated principal is still
open: it needs the auth-design decision, and no auth system is invented
here. Until then, anyone who can reach the API can read or append any
session id they know; the shared-default hole is closed, the isolation
gap is not.)
"""

from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from agent_api.services.ask_agent import (
    AgentServiceError,
    AskAgentError,
    AskAgentService,
    InvalidMessageError,
)


class ChatRequest(BaseModel):
    message: str
    # Required: no default. The UI always generates and sends one; direct
    # API clients must do the same (missing -> 422 from pydantic).
    session_id: str


class ChatResponse(BaseModel):
    reply: str
    tools_used: list[str]
    session_id: str


def _to_http_error(exc: AskAgentError) -> HTTPException:
    if isinstance(exc, InvalidMessageError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


def create_app(
    service: AskAgentService,
    model_label: str,
    cors_origins: tuple[str, ...] | list[str] = (),
) -> FastAPI:
    """Builds the FastAPI app with the use case injected.

    `cors_origins` comes from Settings (CORS_ORIGINS, comma-separated);
    empty means same-origin only — cross-origin browser access is
    opt-in per deployment, never "*" by default.
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
        return {"status": "ok", "model": model_label}

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
    def ask(req: ChatRequest):
        # Sync def, deliberately: FastAPI runs sync endpoints in its
        # threadpool, so a minutes-long model turn cannot block the
        # event loop (the old `async def` awaited nothing and stalled
        # /api/health, /api/ready and /api/chat for the whole turn).
        try:
            reply = service.ask(req.session_id, req.message)
        except AskAgentError as exc:
            raise _to_http_error(exc) from exc
        return ChatResponse(
            reply=reply.text, tools_used=list(reply.tool_uses), session_id=req.session_id
        )

    @app.post("/api/chat")
    async def chat(req: ChatRequest):
        async def event_stream():
            try:
                async for chunk in service.ask_stream(req.session_id, req.message):
                    yield f"data: {json.dumps({'type': chunk.kind, 'payload': chunk.payload})}\n\n"
            except AskAgentError as exc:
                status = 400 if isinstance(exc, InvalidMessageError) else 502
                yield f"data: {json.dumps({'type': 'error', 'status': status, 'error': str(exc)})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.delete("/api/sessions/{session_id}")
    def reset_session(session_id: str):
        """Clear a session's history (fresh start for a bricked session)."""
        try:
            service.reset_session(session_id)
        except AskAgentError as exc:
            raise _to_http_error(exc) from exc
        return {"status": "reset", "session_id": session_id}

    return app
