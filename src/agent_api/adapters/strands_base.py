"""Shared mechanics for Strands-based ModelGateway adapters.

Subclasses provide the model construction, the provider name, the transient
failure classification, and a cheap reachability check. Everything else —
seeding the agent from history, the tool loop, retries, error translation —
lives here, once.

Retry rule for streams: only failures before the first chunk are retried.
Once chunks have been yielded to the caller, retrying would duplicate output,
so the error is raised instead.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from strands import Agent

from agent_api.domain.conversation import ASSISTANT, Conversation, Turn
from agent_api.platform.retry import RetryPolicy, backoff_delay, call_with_retry
from agent_api.ports.model_gateway import ModelGatewayError, StreamChunk


class StrandsGatewayBase:
    """ModelGateway skeleton around a Strands agent."""

    def __init__(
        self,
        *,
        tools: list,
        system_prompt: str,
        retry_policy: RetryPolicy,
    ) -> None:
        self._tools = tools
        self._system_prompt = system_prompt
        self._retry = retry_policy

    # -- subclass hooks -----------------------------------------------------

    def _build_model(self):
        """Construct the provider-specific Strands model."""
        raise NotImplementedError

    def _provider_name(self) -> str:
        raise NotImplementedError

    def _is_transient(self, exc: Exception) -> bool:
        """Whether this provider failure is worth retrying."""
        return False

    def ping(self) -> None:
        """Cheap reachability check; raise ModelGatewayError when down."""
        raise NotImplementedError

    # -- shared mechanics ---------------------------------------------------

    def _build_agent(self, conversation: Conversation) -> Agent:
        messages = [
            {"role": turn.role, "content": [{"text": turn.content}]}
            for turn in conversation.turns
        ]
        return Agent(
            model=self._build_model(),
            messages=messages,
            system_prompt=self._system_prompt,
            tools=self._tools,
            # Explicit None -> null handler. The default would print every
            # streamed token to the server's stdout.
            callback_handler=None,
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

    def _generate_once(self, conversation: Conversation) -> Turn:
        agent = self._build_agent(conversation)
        result = agent()  # no prompt: continue from the seeded history
        return Turn(
            role=ASSISTANT,
            content=self._reply_text(result),
            tool_uses=tuple(self._tools_used(agent)),
        )

    def generate(self, conversation: Conversation) -> Turn:
        try:
            return call_with_retry(
                lambda: self._generate_once(conversation),
                policy=self._retry,
                is_transient=self._is_transient,
            )
        except Exception as exc:  # noqa: BLE001
            raise ModelGatewayError(
                f"{self._provider_name()} call failed: {exc}"
            ) from exc

    async def _stream_once(
        self, conversation: Conversation
    ) -> AsyncIterator[StreamChunk]:
        agent = self._build_agent(conversation)
        async for event in agent.stream_async():  # no prompt: seeded history
            # Text deltas arrive as {"data": "<text>"}; other events carry
            # dicts (message/result) or lifecycle markers — ignore those.
            data = event.get("data")
            if isinstance(data, str) and data:
                yield StreamChunk(kind="text", payload=data)
            if "current_tool_use" in event:
                name = (event["current_tool_use"] or {}).get("name", "")
                if name:
                    yield StreamChunk(kind="tool", payload=name)

    async def stream(
        self, conversation: Conversation
    ) -> AsyncIterator[StreamChunk]:
        attempt = 0
        while True:
            yielded_any = False
            try:
                async for chunk in self._stream_once(conversation):
                    yielded_any = True
                    yield chunk
                return
            except Exception as exc:  # noqa: BLE001
                if (
                    yielded_any
                    or attempt + 1 >= self._retry.max_attempts
                    or not self._is_transient(exc)
                ):
                    raise ModelGatewayError(
                        f"{self._provider_name()} stream failed: {exc}"
                    ) from exc
                attempt += 1
                await asyncio.sleep(backoff_delay(self._retry, attempt))
