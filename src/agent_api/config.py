"""Project configuration — everything comes from the environment."""

from __future__ import annotations

import os


def _sanitize_proxy_env() -> None:
    """Drop proxy env vars before the `ollama` package is imported.

    Some sandboxed environments set proxy variables that break the `ollama`
    Python package's import-time HTTP client (it mis-parses them with httpx).
    The Ollama server runs on localhost, which needs no proxy, so removing
    them is safe here. Only applied for the ollama provider.
    """
    for key in [k for k in os.environ if "proxy" in k.lower()]:
        del os.environ[key]


PROVIDER: str = os.environ.get("AGENT_MODEL_PROVIDER", "ollama")  # ollama | gemini

OLLAMA_HOST: str = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL: str = os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b")

GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")

PORT: int = int(os.environ.get("PORT", "8000"))

if PROVIDER == "ollama":
    _sanitize_proxy_env()


def describe_model() -> str:
    if PROVIDER == "gemini":
        return f"gemini:{GEMINI_MODEL}"
    return f"ollama:{OLLAMA_MODEL}"
