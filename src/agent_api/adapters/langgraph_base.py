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
import json
from typing import AsyncIterator

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from agent_api.domain.conversation import ASSISTANT, USER, Conversation, ToolResult, Turn
from agent_api.platform.retry import RetryPolicy, backoff_delay, call_with_retry
from agent_api.ports.model_gateway import ModelGateway, ModelGatewayError, StreamChunk


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


class LangGraphGatewayBase(ModelGateway):
    """ModelGateway skeleton around a LangGraph ReAct agent.

    Subclassing the port (m-2) so type checkers verify conformance
    instead of relying on duck typing.
    """

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
        # Seed framework-natively: an assistant turn's tool calls become
        # AIMessage tool_calls, each followed by its ToolMessage result —
        # the shape LangGraph itself produces mid-turn. Synthetic ids pair
        # each call with its result; they are history reconstruction,
        # never re-executed.
        messages = []
        for i, turn in enumerate(conversation.turns):
            if turn.role == USER:
                messages.append(HumanMessage(content=turn.content))
            elif turn.role == ASSISTANT:
                if turn.tool_results:
                    messages.append(
                        AIMessage(
                            content=turn.content,
                            tool_calls=[
                                {
                                    "id": f"seed-{i}-{j}",
                                    "name": result.tool,
                                    "args": {},
                                }
                                for j, result in enumerate(turn.tool_results)
                            ],
                        )
                    )
                    for j, result in enumerate(turn.tool_results):
                        messages.append(
                            ToolMessage(
                                content=result.result,
                                tool_call_id=f"seed-{i}-{j}",
                            )
                        )
                else:
                    messages.append(AIMessage(content=turn.content))
        return messages

    @staticmethod
    def _tool_results(messages: list) -> list[ToolResult]:
        # Map tool_call_id -> tool name from the AIMessage tool_calls, then
        # read each ToolMessage's content verbatim.
        names: dict[str, str] = {}
        for message in messages:
            for call in getattr(message, "tool_calls", None) or []:
                if call.get("id") and call.get("name"):
                    names[call["id"]] = call["name"]
        results: list[ToolResult] = []
        for message in messages:
            if isinstance(message, ToolMessage):
                content = message.content
                if not isinstance(content, str):
                    content = json.dumps(content, default=str)
                results.append(
                    ToolResult(
                        tool=names.get(message.tool_call_id, getattr(message, "name", "")),
                        result=content,
                    )
                )
        return results

    @classmethod
    def _final_turn(cls, messages: list) -> Turn:
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
        return Turn(
            role=ASSISTANT,
            content=reply.strip(),
            tool_uses=tuple(tool_uses),
            tool_results=tuple(cls._tool_results(messages)),
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

    def _generate_once(self, conversation: Conversation) -> Turn:
        agent = self._build_agent()
        seeded = self._messages(conversation)
        result = agent.invoke({"messages": seeded})
        # Slice off the seeded prefix: seeded tool_calls/ToolMessages
        # belong to earlier turns, not this one.
        return self._final_turn(result["messages"][len(seeded):])

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
            elif kind == "on_tool_end":
                # Trailing result chunks for the service to persist (see
                # StrandsGatewayBase._stream_once); the UI ignores them.
                output = event["data"].get("output", "")
                if not isinstance(output, str):
                    output = json.dumps(output, default=str)
                yield StreamChunk(
                    kind="tool_result",
                    payload=json.dumps({"tool": event["name"], "result": output}),
                )
