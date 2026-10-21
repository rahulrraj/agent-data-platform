"""Inbound HTTP adapter (controller, §2).

Parses and validates transport input, invokes the use case, and maps
application errors to HTTP status codes (§5). Knows nothing about model
providers — it only sees the AskAgentService.
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
    session_id: str = "default"


class ChatResponse(BaseModel):
    reply: str
    tools_used: list[str]
    session_id: str


def _to_http_error(exc: AskAgentError) -> HTTPException:
    if isinstance(exc, InvalidMessageError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


def create_app(service: AskAgentService, model_label: str) -> FastAPI:
    """Builds the FastAPI app with the use case injected."""
    app = FastAPI(title="Agent API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
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
    async def ask(req: ChatRequest):
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

    return app
