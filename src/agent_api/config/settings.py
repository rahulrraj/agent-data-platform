"""Load, parse, and validate settings (§7).

This is the only place that reads the environment. Everything else receives
typed values via constructors; only main.py reads Settings.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    provider: str  # "ollama" | "gemini"
    framework: str  # "strands" | "langgraph"
    ollama_host: str
    ollama_model: str
    gemini_model: str
    gemini_api_key: str
    ollama_embed_model: str
    gemini_embed_model: str
    retry_max_attempts: int
    retry_base_delay: float
    conversation_store: str  # "memory" | "sqlite" | "postgres"
    sqlite_path: str
    postgres_dsn: str
    embedding_cache_path: str
    capabilities_path: str
    data_connections: dict  # name -> connection config (JSON in DATA_CONNECTIONS)
    port: int

    @classmethod
    def from_env(cls) -> "Settings":
        provider = os.environ.get("AGENT_MODEL_PROVIDER", "ollama")
        if provider not in ("ollama", "gemini"):
            raise ValueError(f"AGENT_MODEL_PROVIDER must be 'ollama' or 'gemini', got {provider!r}")
        framework = os.environ.get("AGENT_FRAMEWORK", "strands")
        if framework not in ("strands", "langgraph"):
            raise ValueError(f"AGENT_FRAMEWORK must be 'strands' or 'langgraph', got {framework!r}")
        store = os.environ.get("CONVERSATION_STORE", "memory")
        if store not in ("memory", "sqlite", "postgres"):
            raise ValueError(f"CONVERSATION_STORE must be 'memory', 'sqlite' or 'postgres', got {store!r}")
        try:
            data_connections = json.loads(os.environ.get("DATA_CONNECTIONS", "{}"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"DATA_CONNECTIONS is not valid JSON: {exc}") from exc
        if not isinstance(data_connections, dict):
            raise ValueError("DATA_CONNECTIONS must be a JSON object")
        return cls(
            provider=provider,
            framework=framework,
            ollama_host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            ollama_model=os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b"),
            gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
            ollama_embed_model=os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
            gemini_embed_model=os.environ.get("GEMINI_EMBED_MODEL", "text-embedding-004"),
            retry_max_attempts=int(os.environ.get("RETRY_MAX_ATTEMPTS", "3")),
            retry_base_delay=float(os.environ.get("RETRY_BASE_DELAY", "0.5")),
            conversation_store=store,
            sqlite_path=os.environ.get("SQLITE_PATH", "conversations.db"),
            postgres_dsn=os.environ.get("POSTGRES_DSN", ""),
            embedding_cache_path=os.environ.get("EMBEDDING_CACHE_PATH", "embedding_cache.db"),
            capabilities_path=os.environ.get(
                "CAPABILITIES_PATH", "registry/capabilities.yaml"
            ),
            data_connections=data_connections,
            port=int(os.environ.get("PORT", "8000")),
        )

    def describe_model(self) -> str:
        if self.provider == "gemini":
            return f"{self.framework}:gemini:{self.gemini_model}"
        return f"{self.framework}:ollama:{self.ollama_model}"
