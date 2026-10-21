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
    port: int

    @classmethod
    def from_env(cls) -> "Settings":
        provider = os.environ.get("AGENT_MODEL_PROVIDER", "ollama")
        if provider not in ("ollama", "gemini"):
            raise ValueError(f"AGENT_MODEL_PROVIDER must be 'ollama' or 'gemini', got {provider!r}")
        settings = cls(
            provider=provider,
            ollama_host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            ollama_model=os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b"),
            gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
            port=int(os.environ.get("PORT", "8000")),
        )
        return settings

    def describe_model(self) -> str:
        if self.provider == "gemini":
            return f"gemini:{self.gemini_model}"
        return f"ollama:{self.ollama_model}"
