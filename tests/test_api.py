"""End-to-end tests for the agent API (in-process; no server needed).

Requires the configured model to be reachable: for the default ollama
provider, start it first (see README). The /api/ask tests drive the real
agent loop, so they take ~30-60s on a small local model.
"""

import os

import pytest
from fastapi.testclient import TestClient

from agent_api.server import app

client = TestClient(app)

NEEDS_MODEL = pytest.mark.skipif(
    os.environ.get("AGENT_MODEL_PROVIDER", "ollama") == "gemini"
    and not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set",
)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "model" in body


def test_ask_rejects_empty_message():
    r = client.post("/api/ask", json={"message": "   ", "session_id": "t"})
    assert r.status_code == 400


@NEEDS_MODEL
def test_ask_uses_tool_and_answers():
    r = client.post(
        "/api/ask",
        json={"message": "What is 6 * 7? Use the calculate tool.", "session_id": "t1"},
    )
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert "42" in body["reply"], body
    assert "calculate" in body["tools_used"], body


@NEEDS_MODEL
def test_session_keeps_history():
    sid = "t2"
    r = client.post(
        "/api/ask",
        json={"message": "What is 6 * 7? Use the calculate tool.", "session_id": sid},
    )
    assert r.status_code == 200, r.text[:300]
    r = client.post(
        "/api/ask",
        json={"message": "What did I just ask you to calculate?", "session_id": sid},
    )
    assert r.status_code == 200, r.text[:300]
    assert "6" in r.json()["reply"], r.json()
