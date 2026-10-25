"""Composition root (§2, §7): construct, inject, wire, start.

This is the only place that reads Settings and the only place that picks
concrete adapters. No business logic lives here. `main.py` is a thin CLI
entrypoint over `build_app()`; `controllers/lambda_handler.py` imports
`build_app` from here — neither imports the other, so there is no
controllers <-> composition cycle (previously the Lambda handler imported
`main`, which imported `controllers.http`).

Gateway classes are imported lazily inside `build_gateway()`, after the
proxy strip below: the `ollama` package builds an HTTP client at import
time, and importing it before the strip would crash on broken proxy env
vars (see ADR 0005). Nothing else in this module needs lazy imports.
"""

from __future__ import annotations

import os
import sys

from fastapi import FastAPI

from agent_api.adapters.bedrock_embedding_gateway import BedrockEmbeddingGateway
from agent_api.adapters.gemini_embedding_gateway import GeminiEmbeddingGateway
from agent_api.adapters.http_capability_executor import HttpCapabilityExecutor
from agent_api.adapters.in_memory_conversation_repository import InMemoryConversationRepository
from agent_api.adapters.ollama_embedding_gateway import OllamaEmbeddingGateway
from agent_api.adapters.postgres_conversation_repository import (
    PostgresConversationRepository,
)
from agent_api.adapters.snowflake_capability_executor import SnowflakeCapabilityExecutor
from agent_api.adapters.sqlite_conversation_repository import SqliteConversationRepository
from agent_api.adapters.sqlite_embedding_cache import SqliteEmbeddingCache
from agent_api.adapters.sqlserver_capability_executor import SqlServerCapabilityExecutor
from agent_api.adapters.yaml_capability_repository import YamlCapabilityRepository
from agent_api.config.settings import Settings
from agent_api.controllers.http import create_app
from agent_api.domain.prompts import build_system_prompt
from agent_api.platform.retry import RetryPolicy
from agent_api.ports.conversation_repository import ConversationRepository
from agent_api.ports.model_gateway import ModelGateway
from agent_api.services.ask_agent import AskAgentService
from agent_api.tools import ToolDeps, load_tools, tool_names


def _strip_proxy_env() -> None:
    """Remove proxy env vars before any Ollama-backed import (ADR 0005).

    The sandbox's proxy values break the `ollama` package's import-time
    HTTP client, and localhost model calls need no proxy. This is the
    single sanctioned place for process-environment handling outside
    settings.py: adapters never touch os.environ.
    """
    for key in [k for k in os.environ if "proxy" in k.lower()]:
        del os.environ[key]


def model_label(settings: Settings) -> str:
    """The display label for the configured model (m-1).

    Canonical home for label construction: Settings.describe_model()
    stays as a thin lookup for callers that already hold a Settings
    object, but the composed app takes its label from here, next to
    the adapter selection it describes.
    """
    return settings.describe_model()


def _validate_lambda_settings(settings: Settings) -> None:
    """Fail fast on Lambda when the documented requirements are unmet (m-11).

    A memory store loses history on every invocation and a sqlite file
    under /tmp is visible to only one execution environment, so neither
    is a silent misconfiguration on Lambda — it is a startup error.
    Likewise the embedding cache must live under /tmp, the only
    writable directory. Outside Lambda (no AWS_LAMBDA_FUNCTION_NAME)
    this is a no-op: local/dev runs may use any store.
    """
    if not settings.lambda_runtime:
        return
    if settings.conversation_store != "postgres":
        raise RuntimeError(
            "On Lambda, CONVERSATION_STORE must be 'postgres' "
            f"(got {settings.conversation_store!r}): memory loses history "
            "every invocation and sqlite is per-execution-environment."
        )
    cache = settings.embedding_cache_path
    if not (cache == "/tmp" or cache.startswith("/tmp/")):
        raise RuntimeError(
            "On Lambda, EMBEDDING_CACHE_PATH must point under /tmp "
            f"(got {cache!r}): /tmp is the only writable directory."
        )


def _embedding_gateway(settings: Settings, retry_policy: RetryPolicy):
    """Single provider-resolution point for embeddings (m-1).

    Previously build_tool_deps() duplicated the provider branch that
    build_gateway() also makes; a future provider now has exactly one
    embedding branch to add here (the framework × provider model matrix
    stays in build_gateway, which is a different decision).
    """
    if settings.provider == "gemini":
        return GeminiEmbeddingGateway(
            settings.gemini_api_key, settings.gemini_embed_model,
            retry_policy=retry_policy,
        )
    if settings.provider == "bedrock":
        return BedrockEmbeddingGateway(
            settings.aws_region, settings.bedrock_embed_model,
            retry_policy=retry_policy,
        )
    return OllamaEmbeddingGateway(
        settings.ollama_host, settings.ollama_embed_model,
        retry_policy=retry_policy,
    )


def build_gateway(
    settings: Settings,
    retry_policy: RetryPolicy,
    tools: list,
    system_prompt: str,
) -> ModelGateway:
    """Pick the gateway from the framework × provider matrix.

    Only the composition root knows the matrix; the service just sees a
    ModelGateway.
    """
    if settings.framework == "langgraph" and settings.provider == "bedrock":
        raise RuntimeError(
            "LangGraph + Bedrock is not built yet (Strands only for bedrock)."
        )
    _strip_proxy_env()
    # Lazy imports: must follow the strip (see module docstring).
    from agent_api.adapters.bedrock_model_gateway import BedrockModelGateway
    from agent_api.adapters.gemini_model_gateway import GeminiModelGateway
    from agent_api.adapters.langgraph_gemini_gateway import LangGraphGeminiGateway
    from agent_api.adapters.langgraph_ollama_gateway import LangGraphOllamaGateway
    from agent_api.adapters.ollama_model_gateway import OllamaModelGateway

    gateway_class = {
        ("strands", "ollama"): OllamaModelGateway,
        ("strands", "gemini"): GeminiModelGateway,
        ("strands", "bedrock"): BedrockModelGateway,
        ("langgraph", "ollama"): LangGraphOllamaGateway,
        ("langgraph", "gemini"): LangGraphGeminiGateway,
    }[(settings.framework, settings.provider)]
    if settings.provider == "gemini" and not settings.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Get a free key at "
            "https://aistudio.google.com/apikey and export GEMINI_API_KEY=<key>"
        )
    kwargs: dict = {}
    if settings.provider == "gemini":
        kwargs = {"api_key": settings.gemini_api_key,
                  "model_id": settings.gemini_model}
    elif settings.provider == "bedrock":
        kwargs = {"region_name": settings.aws_region,
                  "model_id": settings.bedrock_model}
    else:
        kwargs = {"host": settings.ollama_host, "model_id": settings.ollama_model}
    return gateway_class(
        tools=tools,
        system_prompt=system_prompt,
        retry_policy=retry_policy,
        **kwargs,
    )


def build_repository(settings: Settings) -> ConversationRepository:
    if settings.conversation_store == "sqlite":
        return SqliteConversationRepository(settings.sqlite_path)
    if settings.conversation_store == "postgres":
        return PostgresConversationRepository(settings.postgres_dsn)
    return InMemoryConversationRepository()


def build_tool_deps(settings: Settings, retry_policy: RetryPolicy) -> ToolDeps:
    """Wire the data-capability stack: registry + embeddings + executors."""
    registry = YamlCapabilityRepository(settings.capabilities_path)
    embedding_gateway = _embedding_gateway(settings, retry_policy)
    connections = settings.data_connections
    executors = {
        "http": HttpCapabilityExecutor(connections, retry_policy),
        "snowflake": SnowflakeCapabilityExecutor(connections, retry_policy),
        "sqlserver": SqlServerCapabilityExecutor(connections, retry_policy),
    }
    try:
        embedding_cache = SqliteEmbeddingCache(settings.embedding_cache_path)
    except Exception as exc:  # noqa: BLE001 — discovery works uncached, just slower on cold start
        print(f"warning: embedding cache unavailable ({exc}); discovery will re-embed each restart",
              file=sys.stderr)
        embedding_cache = None
    return ToolDeps(
        capability_registry=registry,
        embedding_gateway=embedding_gateway,
        embedding_cache=embedding_cache,
        capability_executors=executors,
    )


def build_app(settings: Settings | None = None) -> FastAPI:
    """Construct the ASGI app without starting a server.

    main() runs it under uvicorn; the Lambda inbound adapter
    (controllers/lambda_handler.py) needs the same app object without a
    server process, so construction lives here, not in main().
    """
    settings = settings or Settings.from_env()
    _validate_lambda_settings(settings)
    retry_policy = RetryPolicy(
        max_attempts=settings.retry_max_attempts,
        base_delay=settings.retry_base_delay,
        max_delay=settings.retry_max_delay,
    )
    deps = build_tool_deps(settings, retry_policy)
    tools = load_tools(deps)
    system_prompt = build_system_prompt(tool_names(tools))
    gateway = build_gateway(settings, retry_policy, tools, system_prompt)
    conversations = build_repository(settings)
    service = AskAgentService(
        gateway=gateway,
        conversations=conversations,
        embedding_gateway=deps.embedding_gateway,
        capability_registry=deps.capability_registry,
    )
    return create_app(
        service, model_label=model_label(settings),
        cors_origins=settings.cors_origins,
    )
