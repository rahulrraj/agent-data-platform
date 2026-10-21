"""Composition root (§2, §7): construct, inject, wire, start.

This is the only place that reads Settings and the only place that picks
concrete adapters. No business logic lives here.
"""

from __future__ import annotations

import sys

import uvicorn

from agent_api.adapters.gemini_model_gateway import GeminiModelGateway
from agent_api.adapters.gemini_embedding_gateway import GeminiEmbeddingGateway
from agent_api.adapters.http_capability_executor import HttpCapabilityExecutor
from agent_api.adapters.langgraph_gemini_gateway import LangGraphGeminiGateway
from agent_api.adapters.langgraph_ollama_gateway import LangGraphOllamaGateway
from agent_api.adapters.memory_conversation_repository import InMemoryConversationRepository
from agent_api.adapters.ollama_model_gateway import OllamaModelGateway
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


def build_gateway(
    settings: Settings,
    retry_policy: RetryPolicy,
    tools: list,
    system_prompt: str,
) -> ModelGateway:
    """Pick the gateway from the framework × provider matrix.

    Only main.py knows the matrix; the service just sees a ModelGateway.
    """
    gateway_class = {
        ("strands", "ollama"): OllamaModelGateway,
        ("strands", "gemini"): GeminiModelGateway,
        ("langgraph", "ollama"): LangGraphOllamaGateway,
        ("langgraph", "gemini"): LangGraphGeminiGateway,
    }[(settings.framework, settings.provider)]
    if settings.provider == "gemini" and not settings.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Get a free key at "
            "https://aistudio.google.com/apikey and export GEMINI_API_KEY=<key>."
        )
    kwargs: dict = {}
    if settings.provider == "gemini":
        kwargs = {"api_key": settings.gemini_api_key,
                  "model_id": settings.gemini_model}
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
    if settings.provider == "gemini":
        embedding_gateway = GeminiEmbeddingGateway(
            settings.gemini_api_key, settings.gemini_embed_model
        )
    else:
        embedding_gateway = OllamaEmbeddingGateway(
            settings.ollama_host, settings.ollama_embed_model
        )
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


def main() -> None:
    settings = Settings.from_env()
    retry_policy = RetryPolicy(
        max_attempts=settings.retry_max_attempts,
        base_delay=settings.retry_base_delay,
    )
    deps = build_tool_deps(settings, retry_policy)
    tools = load_tools(deps)
    system_prompt = build_system_prompt(tool_names(tools))
    gateway = build_gateway(settings, retry_policy, tools, system_prompt)
    conversations = build_repository(settings)
    service = AskAgentService(gateway=gateway, conversations=conversations)
    app = create_app(service, model_label=settings.describe_model())
    uvicorn.run(app, host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
