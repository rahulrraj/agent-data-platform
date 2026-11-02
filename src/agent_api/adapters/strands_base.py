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
import json
import logging
from typing import AsyncIterator

from strands import Agent
from strands.tools.executors.concurrent import ConcurrentToolExecutor

logger = logging.getLogger(__name__)

from agent_api.domain.conversation import ASSISTANT, USER, Conversation, ToolResult, Turn
from agent_api.platform.retry import RetryPolicy, backoff_delay, call_with_retry
from agent_api.ports.model_gateway import ModelGateway, ModelGatewayError, StreamChunk


def build_history_messages(conversation: Conversation) -> list[dict]:
    """Build the Strands/Bedrock message history for a stored conversation.

    Pure function: no Strands/Bedrock imports or I/O, so it is unit-testable
    on its own. The shape is the one Bedrock's Converse API requires, and it
    is also valid for the other Strands providers (Gemini/Ollama) — there is
    no provider-specific branching here:

    * An assistant turn with tool results expands, in transcript order, to
      an assistant message holding ONLY the ``toolUse`` blocks, then a user
      message holding the ``toolResult`` blocks, then an assistant message
      holding the answer text. The answer text never precedes the toolUse
      blocks it followed in the real transcript.
    * A ``toolResult`` block's ``content`` is a LIST of blocks
      (``[{"text": ...}]``), never a bare ``{"text": ...}`` dict.
    * ``toolUseId`` values are the synthetic ``seed-{turn}-{result}``
      pairings, so each result pairs with its call; nothing is re-executed.
    * Back-to-back messages from the same role are merged into one message
      (Bedrock requires alternating roles), preserving block order.
    * Empty turn text produces no text block. A toolResult block is always
      emitted even for an empty result, so every toolUse stays paired.
    * The history always starts with a user message: leading non-user
      messages are dropped, and a dropped toolUse message takes its paired
      toolResult blocks with it (an orphaned toolResult is invalid). No
      user message is ever fabricated — a history with no user turn at
      all yields an empty list.
    """
    messages: list[dict] = []

    def append(role: str, blocks: list[dict]) -> None:
        if not blocks:
            return
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].extend(blocks)
        else:
            messages.append({"role": role, "content": list(blocks)})

    for i, turn in enumerate(conversation.turns):
        if turn.role == ASSISTANT and turn.tool_results:
            append(
                ASSISTANT,
                [
                    {
                        "toolUse": {
                            "toolUseId": f"seed-{i}-{j}",
                            "name": result.tool,
                            "input": {},
                        }
                    }
                    for j, result in enumerate(turn.tool_results)
                ],
            )
            append(
                USER,
                [
                    {
                        "toolResult": {
                            "toolUseId": f"seed-{i}-{j}",
                            "content": [{"text": result.result}],
                        }
                    }
                    for j, result in enumerate(turn.tool_results)
                ],
            )
            if turn.content:
                append(ASSISTANT, [{"text": turn.content}])
        elif turn.content:
            append(turn.role, [{"text": turn.content}])

    # Enforce the leading-user rule without fabricating anything. Each
    # iteration drops at least one message, so this always terminates.
    while messages:
        first = messages[0]
        if (
            first["role"] == USER
            and first["content"]
            and not all("toolResult" in block for block in first["content"])
        ):
            break
        had_tool_use = any("toolUse" in block for block in first["content"])
        messages.pop(0)
        if had_tool_use and messages and messages[0]["role"] == USER:
            kept = [
                block
                for block in messages[0]["content"]
                if "toolResult" not in block
            ]
            if kept:
                messages[0]["content"] = kept
            else:
                messages.pop(0)
    return messages


class StrandsGatewayBase(ModelGateway):
    """ModelGateway skeleton around a Strands agent.

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
        # Seed framework-natively from the stored history; see
        # build_history_messages for the Bedrock-valid shape (toolUse ->
        # toolResult -> answer, list-form toolResult content, merged
        # same-role messages, leading user message). Synthetic toolUseIds
        # pair each call with its result; they are history reconstruction,
        # never re-executed.
        messages = build_history_messages(conversation)
        return Agent(
            model=self._build_model(),
            messages=messages,
            system_prompt=self._system_prompt,
            tools=self._tools,
            # m-9: pin the executor explicitly instead of relying on the
            # SDK default (which happens to also be concurrent). Tools
            # emitted in a single model response run concurrently;
            # successive loop iterations are inherently sequential, so
            # a dependent call (execute after discover) must span loop
            # iterations — the discover/execute provenance set enforces
            # that ordering on top of this.
            tool_executor=ConcurrentToolExecutor(),
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
    def _tools_used(agent: Agent, skip: int = 0) -> list[str]:
        # skip = seeded messages: seeded toolUse blocks belong to earlier
        # turns and must not be attributed to this turn.
        names: list[str] = []
        for message in agent.messages[skip:]:
            for block in message.get("content", []):
                use = block.get("toolUse")
                if use and use.get("name") not in names:
                    names.append(use["name"])
        return names

    @staticmethod
    def _result_text(content) -> str:
        if isinstance(content, dict):
            if "text" in content:
                return str(content["text"])
            if "json" in content:
                return json.dumps(content["json"], default=str)
            return json.dumps(content, default=str)
        if isinstance(content, list):
            return "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        return str(content)

    @classmethod
    def _tool_results(cls, agent: Agent, skip: int = 0) -> list[ToolResult]:
        # toolUse blocks and their toolResult blocks appear in order, so
        # pairing them positionally reconstructs (tool, result) pairs.
        names: list[str] = []
        results: list[str] = []
        for message in agent.messages[skip:]:
            for block in message.get("content", []):
                use = block.get("toolUse")
                if use and use.get("name"):
                    names.append(use["name"])
                outcome = block.get("toolResult")
                if outcome is not None:
                    results.append(cls._result_text(outcome.get("content", "")))
        return [
            ToolResult(tool=name, result=text)
            for name, text in zip(names, results)
        ]

    def _generate_once(self, conversation: Conversation) -> Turn:
        agent = self._build_agent(conversation)
        seeded = len(agent.messages)
        result = agent()  # no prompt: continue from the seeded history
        return Turn(
            role=ASSISTANT,
            content=self._reply_text(result),
            tool_uses=tuple(self._tools_used(agent, seeded)),
            tool_results=tuple(self._tool_results(agent, seeded)),
        )

    def _transient_flag(self, exc: Exception) -> bool:
        try:
            return self._is_transient(exc)
        except Exception:  # noqa: BLE001 — classification must not mask the failure
            return False

    def generate(self, conversation: Conversation) -> Turn:
        try:
            return call_with_retry(
                lambda: self._generate_once(conversation),
                policy=self._retry,
                is_transient=self._is_transient,
            )
        except Exception as exc:  # noqa: BLE001
            # m-19: a broad except used to convert programming errors
            # (tool-translation bugs, prompt bugs) into provider 502s
            # indistinguishable from real outages. The contract is
            # kept (callers still see ModelGatewayError), but the
            # original exception type and its transient classification
            # are logged distinctly so a 502 can be triaged: a
            # provider SDK type points at the provider, anything else
            # points at this codebase.
            logger.warning(
                "%s call failed: %s (transient=%s): %s",
                self._provider_name(), type(exc).__name__,
                self._transient_flag(exc), exc,
            )
            raise ModelGatewayError(
                f"{self._provider_name()} call failed: {exc}"
            ) from exc

    async def _stream_once(
        self, conversation: Conversation
    ) -> AsyncIterator[StreamChunk]:
        agent = self._build_agent(conversation)
        seeded = len(agent.messages)
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
        # Trailing result chunks: the service persists them so the next
        # turn can see this turn's tool outputs. The UI ignores unknown
        # chunk kinds, so these are harmless to the stream display.
        for result in self._tool_results(agent, seeded):
            yield StreamChunk(
                kind="tool_result",
                payload=json.dumps({"tool": result.tool, "result": result.result}),
            )

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
                    logger.warning(
                        "%s stream failed: %s (transient=%s): %s",
                        self._provider_name(), type(exc).__name__,
                        self._transient_flag(exc), exc,
                    )
                    raise ModelGatewayError(
                        f"{self._provider_name()} stream failed: {exc}"
                    ) from exc
                attempt += 1
                await asyncio.sleep(backoff_delay(self._retry, attempt))
