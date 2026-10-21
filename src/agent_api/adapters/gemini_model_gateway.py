"""Gemini implementation of the ModelGateway port (<Technology><PortName>, §2).

Same stateless-per-call shape as OllamaModelGateway; only the model differs.
"""

from __future__ import annotations

from typing import AsyncIterator

from strands import Agent

from agent_api.domain.conversation import ASSISTANT, Conversation, Turn
from agent_api.ports.model_gateway import ModelGatewayError, StreamChunk


class GeminiModelGateway:
    """ModelGateway backed by Gemini via Google AI Studio."""

    def __init__(self, api_key: str, model_id: str, tools: list, system_prompt: str) -> None:
        self._api_key = api_key
        self._model_id = model_id
        self._tools = tools
        self._system_prompt = system_prompt

    def _build_agent(self, conversation: Conversation) -> Agent:
        # Local import: only this adapter needs the gemini provider package.
        from strands.models.gemini import GeminiModel

        model = GeminiModel(
            model_id=self._model_id,
            client_args={"api_key": self._api_key},
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
            raise ModelGatewayError(f"gemini call failed: {exc}") from exc
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
            raise ModelGatewayError(f"gemini stream failed: {exc}") from exc
