"""Composition root (§2, §7): construct, inject, wire, start.

This is the only place that reads Settings and the only place that picks
concrete adapters. No business logic lives here.
"""

from __future__ import annotations

import uvicorn

from agent_api.adapters.gemini_model_gateway import GeminiModelGateway
from agent_api.adapters.memory_conversation_repository import InMemoryConversationRepository
from agent_api.adapters.ollama_model_gateway import OllamaModelGateway
from agent_api.adapters.sqlite_conversation_repository import SqliteConversationRepository
from agent_api.config.settings import Settings
from agent_api.controllers.http import create_app
from agent_api.domain.prompts import build_system_prompt
from agent_api.domain.tools import load_tools, tool_names
from agent_api.platform.retry import RetryPolicy
from agent_api.ports.conversation_repository import ConversationRepository
from agent_api.ports.model_gateway import ModelGateway
from agent_api.services.ask_agent import AskAgentService


def build_gateway(settings: Settings, retry_policy: RetryPolicy) -> ModelGateway:
    tools = load_tools()
    system_prompt = build_system_prompt(tool_names(tools))
    if settings.provider == "gemini":
        if not settings.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Get a free key at "
                "https://aistudio.google.com/apikey and export GEMINI_API_KEY=<key>."
            )
        return GeminiModelGateway(
            api_key=settings.gemini_api_key,
            model_id=settings.gemini_model,
            tools=tools,
            system_prompt=system_prompt,
            retry_policy=retry_policy,
        )
    return OllamaModelGateway(
        host=settings.ollama_host,
        model_id=settings.ollama_model,
        tools=tools,
        system_prompt=system_prompt,
        retry_policy=retry_policy,
    )


def build_repository(settings: Settings) -> ConversationRepository:
    if settings.conversation_store == "sqlite":
        return SqliteConversationRepository(settings.sqlite_path)
    return InMemoryConversationRepository()


def main() -> None:
    settings = Settings.from_env()
    retry_policy = RetryPolicy(
        max_attempts=settings.retry_max_attempts,
        base_delay=settings.retry_base_delay,
    )
    gateway = build_gateway(settings, retry_policy)
    conversations = build_repository(settings)
    service = AskAgentService(gateway=gateway, conversations=conversations)
    app = create_app(service, model_label=settings.describe_model())
    uvicorn.run(app, host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
