"""FastAPI service exposing the Strands agent over HTTP.

Endpoints:
  GET  /api/health  -> service + model status
  POST /api/chat    -> {message, session_id} -> streams the reply as SSE
  POST /api/ask     -> {message, session_id} -> full reply as JSON

Each session_id gets its own agent, so conversation history persists per client.
"""

import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent_api import config
from agent_api.agent import create_agent

app = FastAPI(title="Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


class ChatResponse(BaseModel):
    reply: str
    tools_used: list[str]
    session_id: str


# One agent per session so conversations persist.
_sessions: dict[str, object] = {}


def get_agent(session_id: str):
    if session_id not in _sessions:
        _sessions[session_id] = create_agent()
    return _sessions[session_id]


def tools_used_by(agent) -> list[str]:
    """Names of tools called in the agent's conversation so far."""
    names: list[str] = []
    for message in agent.messages:
        for block in message.get("content", []):
            use = block.get("toolUse")
            if use and use.get("name") not in names:
                names.append(use["name"])
    return names


@app.get("/api/health")
def health():
    return {"status": "ok", "provider": config.PROVIDER, "model": config.describe_model()}


async def event_stream(agent, message: str):
    """Streams the agent's reply as Server-Sent Events."""
    try:
        async for event in agent.stream_async(message):
            delta = (event.get("contentBlockDelta") or {}).get("delta") or {}
            if "text" in delta:
                yield f"data: {json.dumps({'type': 'text', 'text': delta['text']})}\n\n"
            if "current_tool_use" in event:
                name = (event["current_tool_use"] or {}).get("name", "")
                yield f"data: {json.dumps({'type': 'tool', 'name': name})}\n\n"
            if "error" in event:
                yield f"data: {json.dumps({'type': 'error', 'error': str(event['error'])})}\n\n"
    except Exception as exc:  # noqa: BLE001
        yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"
    yield "data: [DONE]\n\n"


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """Chat with the agent; streams text + tool-use events as SSE."""
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message is required")
    try:
        agent = get_agent(req.session_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return StreamingResponse(
        event_stream(agent, req.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/ask", response_model=ChatResponse)
async def ask(req: ChatRequest):
    """Chat with the agent; returns the full reply as JSON (no streaming)."""
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message is required")
    try:
        agent = get_agent(req.session_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    try:
        result = agent(req.message)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    text = "".join(
        block.get("text", "") for block in result.message.get("content", []) if "text" in block
    )
    return ChatResponse(reply=text, tools_used=tools_used_by(agent), session_id=req.session_id)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.PORT)


if __name__ == "__main__":
    main()
