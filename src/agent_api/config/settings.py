"""Load, parse, and validate settings (§7).

This is the only place that reads the environment. Everything else receives
typed values via constructors; only main.py reads Settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    provider: str  # "ollama" | "gemini"
    ollama_host: str
    ollama_model: str
    gemini_model: str
    gemini_api_key: str
    retry_max_attempts: int
    retry_base_delay: float
    conversation_store: str  # "memory" | "sqlite"
    sqlite_path: str
    port: int

    @classmethod
    def from_env(cls) -> "Settings":
        provider = os.environ.get("AGENT_MODEL_PROVIDER", "ollama")
        if provider not in ("ollama", "gemini"):
            raise ValueError(f"AGENT_MODEL_PROVIDER must be 'ollama' or 'gemini', got {provider!r}")
        store = os.environ.get("CONVERSATION_STORE", "memory")
        if store not in ("memory", "sqlite"):
            raise ValueError(f"CONVERSATION_STORE must be 'memory' or 'sqlite', got {store!r}")
        return cls(
            provider=provider,
            ollama_host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            ollama_model=os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b"),
            gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
            retry_max_attempts=int(os.environ.get("RETRY_MAX_ATTEMPTS", "3")),
            retry_base_delay=float(os.environ.get("RETRY_BASE_DELAY", "0.5")),
            conversation_store=store,
            sqlite_path=os.environ.get("SQLITE_PATH", "conversations.db"),
            port=int(os.environ.get("PORT", "8000")),
        )

    def describe_model(self) -> str:
        if self.provider == "gemini":
            return f"gemini:{self.gemini_model}"
        return f"ollama:{self.ollama_model}"
