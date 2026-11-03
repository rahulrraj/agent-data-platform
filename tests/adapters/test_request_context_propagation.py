"""No-leakage suite for the per-turn request context (ADR 0008).

The request context (principal + raw user token) is a context variable
set around each gateway call. The Strands path runs tools through the
context-propagating executor, so a tool must see the current turn's
token, never a previous turn's, and never leak a value it sets into a
later turn — including across reused pool threads. The LangGraph path
has no executor to pin; its tools run on the same thread / asyncio
context, so the same visibility must hold there with no code change.

No live models, no network: a scripted fake Strands model and a fake
LangChain chat model drive the real gateways end to end. The tokens
here are obviously fake placeholders, never real credentials.
"""

from __future__ import annotations

import contextvars
import time

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from strands import Agent, tool
from strands.models.model import Model
from strands.tools.executors.concurrent import ConcurrentToolExecutor

from agent_api.adapters.langgraph_base import LangGraphGatewayBase
from agent_api.adapters.strands_base import (
    ContextPropagatingToolExecutor,
    StrandsGatewayBase,
)
from agent_api.domain.conversation import USER, Conversation, Turn
from agent_api.platform.request_context import get_request_context, request_context
from agent_api.platform.retry import RetryPolicy

FAKE_TOKEN_A = "fake-user-token-A"
FAKE_TOKEN_B = "fake-user-token-B"


# --- Fake Strands model ----------------------------------------------------

class _ScriptedModel(Model):
    """Yields one scripted response per stream() call.

    Each step is {"tools": [names...], "text": "..."}: the model emits
    the tool uses (if any), then the text, then stops with tool_use /
    end_turn accordingly.
    """

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def get_config(self):
        return {}

    def update_config(self, **kwargs):
        pass

    async def structured_output(self, *args, **kwargs):
        raise NotImplementedError

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        step = self._script[self.calls]
        self.calls += 1
        yield {"messageStart": {"role": "assistant"}}
        for i, name in enumerate(step.get("tools", [])):
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": f"u{i}", "name": name}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": "{}"}}}}
            yield {"contentBlockStop": {}}
        if step.get("text"):
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": step["text"]}}}
            yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use" if step.get("tools") else "end_turn"}}


def _turn_script(n_turns, tool_name):
    """n turns, each: one tool call, then a text answer."""
    script = []
    for _ in range(n_turns):
        script.append({"tools": [tool_name]})
        script.append({"tools": [], "text": "done"})
    return script


def _recording_tool(seen, probe_var):
    """Sync tool recording the request token (and the probe var's value).

    It then POISONS the probe var: if context copies were shared with
    later turns, the next turn would read POISON instead of None.
    """

    @tool
    def record_token() -> str:
        ctx = get_request_context()
        seen.append((ctx.token if ctx else None, probe_var.get()))
        probe_var.set("POISON")
        return "ok"

    return record_token


def _run_strands_turns(agent, tokens):
    for token in tokens:
        if token is None:
            agent("hi")
        else:
            with request_context("user-1", token):
                agent("hi")


# --- (a) current turn's token is visible -----------------------------------

def test_tool_sees_current_turn_token():
    seen = []
    probe_var = contextvars.ContextVar("probe_a", default=None)
    agent = Agent(
        model=_ScriptedModel(_turn_script(1, "record_token")),
        tools=[_recording_tool(seen, probe_var)],
        tool_executor=ContextPropagatingToolExecutor(),
        callback_handler=None,
        system_prompt="x",
    )
    _run_strands_turns(agent, [FAKE_TOKEN_A])
    assert seen == [(FAKE_TOKEN_A, None)]


# --- (b) sequential turns see their own token -------------------------------

def test_sequential_turns_see_their_own_tokens():
    seen = []
    probe_var = contextvars.ContextVar("probe_b", default=None)
    agent = Agent(
        model=_ScriptedModel(_turn_script(2, "record_token")),
        tools=[_recording_tool(seen, probe_var)],
        tool_executor=ContextPropagatingToolExecutor(),
        callback_handler=None,
        system_prompt="x",
    )
    _run_strands_turns(agent, [FAKE_TOKEN_A, FAKE_TOKEN_B])
    assert [token for token, _ in seen] == [FAKE_TOKEN_A, FAKE_TOKEN_B]


# --- (c) no leakage across turns / reused pool threads ----------------------

def test_no_token_turn_after_token_turn_sees_none_over_many_turns():
    seen = []
    probe_var = contextvars.ContextVar("probe_c", default=None)
    tokens = [FAKE_TOKEN_A, None, FAKE_TOKEN_B, None] * 4  # 16 turns, one executor
    agent = Agent(
        model=_ScriptedModel(_turn_script(len(tokens), "record_token")),
        tools=[_recording_tool(seen, probe_var)],
        tool_executor=ContextPropagatingToolExecutor(),
        callback_handler=None,
        system_prompt="x",
    )
    _run_strands_turns(agent, tokens)
    assert [token for token, _ in seen] == tokens
    # The poison set by each tool never reaches the next turn, even
    # though sync tools run on reused pool threads.
    assert all(probe is None for _, probe in seen)
    # The caller's own context is untouched by the tools.
    assert get_request_context() is None
    assert probe_var.get() is None


# --- concurrent tools cannot see each other's writes ------------------------

def test_concurrent_tools_are_isolated_from_each_other():
    probe_var = contextvars.ContextVar("probe_d", default=None)
    seen = []

    @tool
    def setter_tool() -> str:
        probe_var.set("POISON")
        time.sleep(0.2)
        return "set"

    @tool
    def reader_tool() -> str:
        time.sleep(0.05)  # setter has already written by now, if shared
        ctx = get_request_context()
        seen.append((ctx.token if ctx else None, probe_var.get()))
        return "read"

    agent = Agent(
        model=_ScriptedModel([
            {"tools": ["setter_tool", "reader_tool"]},
            {"tools": [], "text": "done"},
        ]),
        tools=[setter_tool, reader_tool],
        tool_executor=ContextPropagatingToolExecutor(),
        callback_handler=None,
        system_prompt="x",
    )
    with request_context("user-1", FAKE_TOKEN_A):
        agent("hi")
    # The reader sees the turn's token but NOT the setter's write:
    # each tool ran in its own copy of the calling context.
    assert seen == [(FAKE_TOKEN_A, None)]


# --- error handling preserved ----------------------------------------------

def test_tool_error_still_surfaces_as_error_result():
    @tool
    def failing_tool() -> str:
        raise ValueError("boom")

    agent = Agent(
        model=_ScriptedModel([
            {"tools": ["failing_tool"]},
            {"tools": [], "text": "done"},
        ]),
        tools=[failing_tool],
        tool_executor=ContextPropagatingToolExecutor(),
        callback_handler=None,
        system_prompt="x",
    )
    with request_context("user-1", FAKE_TOKEN_A):
        result = agent("hi")
    assert "done" in "".join(
        b.get("text", "") for b in result.message.get("content", [])
    )
    # The failure became an error tool result, not a crash: the
    # toolResult content carries the exception text.
    assert any("boom" in str(m) for m in agent.messages)


# --- executor pinning ---------------------------------------------------------

def test_build_agent_pins_propagating_executor(monkeypatch):
    captured = {}

    class _CapturingAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _Probe(StrandsGatewayBase):
        def _build_model(self):
            return object()

        def _provider_name(self):
            return "probe"

    monkeypatch.setattr("agent_api.adapters.strands_base.Agent", _CapturingAgent)
    probe = _Probe(tools=[], system_prompt="s", retry_policy=RetryPolicy())
    probe._build_agent(Conversation(id="c", turns=[Turn(role=USER, content="hi")]))
    executor = captured["tool_executor"]
    assert type(executor) is ContextPropagatingToolExecutor
    # Still a ConcurrentToolExecutor, so the m-9 pin test keeps passing.
    assert isinstance(executor, ConcurrentToolExecutor)


# --- (d) context reset, even on exception -----------------------------------

def test_request_context_resets_after_exit_even_on_exception():
    assert get_request_context() is None
    with request_context("user-1", FAKE_TOKEN_A):
        assert get_request_context().token == FAKE_TOKEN_A
    assert get_request_context() is None

    with pytest.raises(RuntimeError, match="boom"):
        with request_context("user-1", FAKE_TOKEN_A):
            assert get_request_context().token == FAKE_TOKEN_A
            raise RuntimeError("boom")
    assert get_request_context() is None


# --- (e) LangGraph path: same visibility, no executor change -----------------
#
# LangGraph has no tool-executor pin (see langgraph_base._build_agent):
# tools run through LangChain's ToolNode on the same thread / asyncio
# context, so the context variable propagates without any change. This
# test pins that behavior with a fake chat model — no live Ollama.

def _langgraph_gateway(seen):
    calls = [0]

    @tool
    def record_token() -> str:
        ctx = get_request_context()
        seen.append(ctx.token if ctx else None)
        return "ok"

    class _FakeChat(BaseChatModel):
        @property
        def _llm_type(self):
            return "fake"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            calls[0] += 1
            if calls[0] % 2 == 1:
                msg = AIMessage(
                    content="",
                    tool_calls=[{"id": "c1", "name": "record_token", "args": {}}],
                )
            else:
                msg = AIMessage(content="done")
            return ChatResult(generations=[ChatGeneration(message=msg)])

    class _Probe(LangGraphGatewayBase):
        def _build_chat_model(self):
            return _FakeChat()

        def _provider_name(self):
            return "probe"

    return _Probe(
        tools=[record_token],
        system_prompt="s",
        retry_policy=RetryPolicy(max_attempts=1),
    )


def test_langgraph_tool_sees_request_context_token():
    seen = []
    gateway = _langgraph_gateway(seen)
    conv = Conversation(id="t", turns=[Turn(role=USER, content="hi")])

    with request_context("user-1", FAKE_TOKEN_A):
        gateway.generate(conv)
    with request_context("user-1", FAKE_TOKEN_B):
        gateway.generate(conv)
    gateway.generate(conv)  # no token: must see none, not the last turn's

    assert seen == [FAKE_TOKEN_A, FAKE_TOKEN_B, None]
