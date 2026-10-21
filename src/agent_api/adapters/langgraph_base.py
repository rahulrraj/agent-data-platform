"""Shared mechanics for LangGraph-based ModelGateway adapters.

Same port contract as StrandsGatewayBase: seed from conversation history, run
one turn, translate to Turn / StreamChunk, retry transient failures, raise
ModelGatewayError otherwise.

Tools are defined once as Strands @tool functions (the canonical form) and
translated here into LangChain StructuredTools — name, description, and JSON
schema come straight from the Strands tool spec, so no tool is ever rewritten
for a framework.

Stateless per call like the Strands side: no checkpointer, no persisted
graph state. History stays owned by the ConversationRepository.

Retry rule for streams: only failures before the first chunk are retried.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool

from agent_api.domain.conversation import ASSISTANT, USER, Conversation, Turn
from agent_api.platform.retry import RetryPolicy, backoff_delay, call_with_retry
from agent_api.ports.model_gateway import ModelGatewayError, StreamChunk


def to_langchain_tool(strands_tool) -> StructuredTool:
    """Translate one Strands @tool function into a LangChain StructuredTool.

    The Strands tool stays the canonical definition; this is a mechanical
    translation at the framework boundary. The wrapped function is called
    with kwargs, exactly as Strands would call it.
    """
    spec = strands_tool.tool_spec

    def _invoke(**kwargs):
        return strands_tool(**kwargs)

    return StructuredTool.from_function(
        func=_invoke,
        name=spec["name"],
        description=spec["description"],
        args_schema=spec["inputSchema"]["json"],
    )


class LangGraphGatewayBase:
    """ModelGateway skeleton around a LangGraph ReAct agent."""

    def __init__(
        self,
        *,
        tools: list,
        system_prompt: str,
        retry_policy: RetryPolicy,
    ) -> None:
        self._lc_tools = [to_langchain_tool(t) for t in tools]
        self._system_prompt = system_prompt
        self._retry = retry_policy

    # -- subclass hooks -----------------------------------------------------

    def _build_chat_model(self):
        """Construct the provider-specific LangChain chat model."""
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

    def _build_agent(self):
        return create_agent(
            self._build_chat_model(),
            self._lc_tools,
            system_prompt=self._system_prompt,
        )

    @staticmethod
    def _messages(conversation: Conversation) -> list:
        messages = []
        for turn in conversation.turns:
            if turn.role == USER:
                messages.append(HumanMessage(content=turn.content))
            elif turn.role == ASSISTANT:
                messages.append(AIMessage(content=turn.content))
        return messages

    @staticmethod
    def _final_turn(messages: list) -> Turn:
        tool_uses: list[str] = []
        for message in messages:
            for call in getattr(message, "tool_calls", None) or []:
                name = call.get("name", "")
                if name and name not in tool_uses:
                    tool_uses.append(name)
        reply = ""
        for message in reversed(messages):
            if isinstance(message, AIMessage) and message.content:
                reply = str(message.content)
                break
        return Turn(role=ASSISTANT, content=reply.strip(), tool_uses=tuple(tool_uses))

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

    def _generate_once(self, conversation: Conversation) -> Turn:
        agent = self._build_agent()
        result = agent.invoke({"messages": self._messages(conversation)})
        return self._final_turn(result["messages"])

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

    async def _stream_once(
        self, conversation: Conversation
    ) -> AsyncIterator[StreamChunk]:
        agent = self._build_agent()
        async for event in agent.astream_events(
            {"messages": self._messages(conversation)}, version="v2"
        ):
            kind = event["event"]
            if kind == "on_chat_model_stream":
                text = event["data"]["chunk"].content
                if text:
                    yield StreamChunk(kind="text", payload=str(text))
            elif kind == "on_tool_start":
                yield StreamChunk(kind="tool", payload=event["name"])
