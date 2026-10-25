"""Load, parse, and validate settings (§7).

This is the only place that reads the environment. Everything else receives
typed values via constructors; only the composition root reads Settings.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


def _parse_int(name: str, raw: str, *, minimum: int | None = None,
                maximum: int | None = None) -> int:
    """Parse an env var as an int, naming the variable on failure (m-13).

    A bare int() ValueError at startup named neither the variable nor the
    value; a bad RETRY_BASE_DELAY used to surface later as a misleading
    provider failure from time.sleep.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}, got {value}")
    return value


def _parse_float(name: str, raw: str, *, minimum: float | None = None) -> float:
    """Parse an env var as a float, naming the variable on failure (m-13)."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {raw!r}") from None
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    provider: str  # "ollama" | "gemini" | "bedrock"
    framework: str  # "strands" | "langgraph"
    ollama_host: str
    ollama_model: str
    gemini_model: str
    gemini_api_key: str
    ollama_embed_model: str
    gemini_embed_model: str
    aws_region: str
    bedrock_model: str
    bedrock_embed_model: str
    retry_max_attempts: int
    retry_base_delay: float
    retry_max_delay: float
    conversation_store: str  # "memory" | "sqlite" | "postgres"
    sqlite_path: str
    postgres_dsn: str
    embedding_cache_path: str
    capabilities_path: str
    data_connections: dict  # name -> connection config (JSON in DATA_CONNECTIONS)
    port: int
    cors_origins: tuple[str, ...]  # from CORS_ORIGINS, comma-separated;
        # empty = same-origin only (no cross-origin browser access)
    lambda_runtime: bool  # True when AWS_LAMBDA_FUNCTION_NAME is set:
        # the composition root then enforces the Lambda store/cache
        # requirements (m-11) instead of merely documenting them.

    @classmethod
    def from_env(cls) -> "Settings":
        provider = os.environ.get("AGENT_MODEL_PROVIDER", "ollama")
        if provider not in ("ollama", "gemini", "bedrock"):
            raise ValueError(f"AGENT_MODEL_PROVIDER must be 'ollama', 'gemini' or 'bedrock', got {provider!r}")
        framework = os.environ.get("AGENT_FRAMEWORK", "strands")
        if framework not in ("strands", "langgraph"):
            raise ValueError(f"AGENT_FRAMEWORK must be 'strands' or 'langgraph', got {framework!r}")
        store = os.environ.get("CONVERSATION_STORE", "memory")
        if store not in ("memory", "sqlite", "postgres"):
            raise ValueError(f"CONVERSATION_STORE must be 'memory', 'sqlite' or 'postgres', got {store!r}")
        # An empty DATA_CONNECTIONS (e.g. an unset GitHub secret expanding
        # to "") means "no data connections", not invalid JSON: treating
        # "" as "{}" keeps a no-connections deploy bootable.
        raw_connections = os.environ.get("DATA_CONNECTIONS", "{}")
        if not raw_connections.strip():
            raw_connections = "{}"
        try:
            data_connections = json.loads(raw_connections)
        except json.JSONDecodeError as exc:
            raise ValueError(f"DATA_CONNECTIONS is not valid JSON: {exc}") from exc
        if not isinstance(data_connections, dict):
            raise ValueError("DATA_CONNECTIONS must be a JSON object")
        return cls(
            provider=provider,
            framework=framework,
            ollama_host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            ollama_model=os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b"),
            gemini_model=os.environ.get("GEMINI_MODEL", "gemini-flash-latest"),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
            ollama_embed_model=os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
            gemini_embed_model=os.environ.get("GEMINI_EMBED_MODEL", "gemini-embedding-001"),
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
            bedrock_model=os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-5-5"),
            bedrock_embed_model=os.environ.get("BEDROCK_EMBED_MODEL", "amazon.titan-embed-text-v2:0"),
            retry_max_attempts=_parse_int(
                "RETRY_MAX_ATTEMPTS",
                os.environ.get("RETRY_MAX_ATTEMPTS", "3"),
                minimum=1,
            ),
            retry_base_delay=_parse_float(
                "RETRY_BASE_DELAY",
                os.environ.get("RETRY_BASE_DELAY", "0.5"),
                minimum=0,
            ),
            retry_max_delay=_parse_float(
                "RETRY_MAX_DELAY",
                os.environ.get("RETRY_MAX_DELAY", "8.0"),
                minimum=0,
            ),
            conversation_store=store,
            sqlite_path=os.environ.get("SQLITE_PATH", "conversations.db"),
            postgres_dsn=os.environ.get("POSTGRES_DSN", ""),
            embedding_cache_path=os.environ.get("EMBEDDING_CACHE_PATH", "embedding_cache.db"),
            capabilities_path=os.environ.get(
                "CAPABILITIES_PATH", "registry/capabilities.yaml"
            ),
            data_connections=data_connections,
            port=_parse_int(
                "PORT", os.environ.get("PORT", "8000"), minimum=1, maximum=65535
            ),
            cors_origins=tuple(
                origin.strip()
                for origin in os.environ.get("CORS_ORIGINS", "").split(",")
                if origin.strip()
            ),
            lambda_runtime="AWS_LAMBDA_FUNCTION_NAME" in os.environ,
        )

    def describe_model(self) -> str:
        """Display label only (m-1): a table lookup, not a branch that
        picks an adapter. Adapter selection lives solely in the
        composition root (composition.build_gateway /
        build_tool_deps); this method never influences it.
        """
        model = {
            "ollama": self.ollama_model,
            "gemini": self.gemini_model,
            "bedrock": self.bedrock_model,
        }[self.provider]
        return f"{self.framework}:{self.provider}:{model}"
