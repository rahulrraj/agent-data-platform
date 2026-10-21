"""Ollama implementation of the ModelGateway port (<Technology><PortName>, §2).

Stateless per call: it seeds a Strands agent from the conversation history and
runs one turn. History itself stays owned by the ConversationRepository, so the
adapter never accumulates hidden state.

Note: only the text of prior turns is replayed, not full tool-call detail —
enough for follow-up questions, not a byte-exact transcript.
"""

from __future__ import annotations

import os
from typing import AsyncIterator

from strands import Agent

from agent_api.domain.conversation import ASSISTANT, Conversation, Turn
from agent_api.ports.model_gateway import ModelGatewayError, StreamChunk


def _sanitize_proxy_env() -> None:
    """Drop proxy env vars before the `ollama` package is imported.

    Some sandboxed environments set proxy variables that break the `ollama`
    Python package's import-time HTTP client (it mis-parses them with httpx).
    The Ollama server runs on localhost, which needs no proxy, so removing
    them is safe here.
    """
    for key in [k for k in os.environ if "proxy" in k.lower()]:
        del os.environ[key]


# Runs at import: must execute before anything imports the `ollama` package
# (strands.models.ollama creates its HTTP client at import time).
_sanitize_proxy_env()


class OllamaModelGateway:
    """ModelGateway backed by a local Ollama server."""

    def __init__(self, host: str, model_id: str, tools: list, system_prompt: str) -> None:
        self._host = host
        self._model_id = model_id
        self._tools = tools
        self._system_prompt = system_prompt

    def _build_agent(self, conversation: Conversation) -> Agent:
        # Local import: only this adapter needs the ollama provider package.
        from strands.models.ollama import OllamaModel

        model = OllamaModel(
            host=self._host,
            model_id=self._model_id,
            temperature=0,  # small models go off the rails otherwise
            max_tokens=512,
        )
        messages = [
            {"role": turn.role, "content": [{"text": turn.content}]}
            for turn in conversation.turns
        ]
        return Agent(
            model=model,
            messages=messages,
            system_prompt=self._system_prompt,
            tools=self._tools,
        )

    @staticmethod
    def _reply_text(result) -> str:
        return "".join(
            block.get("text", "")
            for block in result.message.get("content", [])
            if "text" in block
        )

    @staticmethod
    def _tools_used(agent: Agent) -> list[str]:
        names: list[str] = []
        for message in agent.messages:
            for block in message.get("content", []):
                use = block.get("toolUse")
                if use and use.get("name") not in names:
                    names.append(use["name"])
        return names

    def generate(self, conversation: Conversation) -> Turn:
        try:
            agent = self._build_agent(conversation)
            result = agent()  # no prompt: continue from the seeded history
        except Exception as exc:  # noqa: BLE001
            raise ModelGatewayError(f"ollama call failed: {exc}") from exc
        return Turn(
            role=ASSISTANT,
            content=self._reply_text(result),
            tool_uses=tuple(self._tools_used(agent)),
        )

    async def stream(self, conversation: Conversation) -> AsyncIterator[StreamChunk]:
        try:
            agent = self._build_agent(conversation)
            async for event in agent.stream_async():  # no prompt: seeded history
                delta = (event.get("contentBlockDelta") or {}).get("delta") or {}
                if "text" in delta:
                    yield StreamChunk(kind="text", payload=delta["text"])
                if "current_tool_use" in event:
                    name = (event["current_tool_use"] or {}).get("name", "")
                    yield StreamChunk(kind="tool", payload=name)
        except Exception as exc:  # noqa: BLE001
            raise ModelGatewayError(f"ollama stream failed: {exc}") from exc
