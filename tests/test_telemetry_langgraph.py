"""LangGraph-native OTel detail (ADR 0010).

OpenInference instruments the LangGraph adapter's LangChain runnables
through the global provider. These tests pin, empirically and against
a stub model, what that buys and what it must never do:

* a turn emits per-step spans (LangGraph root / chain / LLM / tool) on both
  the sync and streaming paths, nested under the service `agent.turn`
  span;
* no span attribute carries prompt, completion, tool-argument, or
  tool-result content — sentinel strings placed in the user message,
  the tool argument, the tool result, and the system prompt must not
  appear in any span, and input/output values are the redaction
  marker;
* token counts on the LLM span are the real scripted values, never
  invented.

No live models, no network, no endpoint: the instrumentor is bound
to an explicit in-memory provider, never to global OTel state.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from strands import tool

from agent_api.adapters.langgraph_base import LangGraphGatewayBase
from agent_api.domain.conversation import USER, Conversation, Turn
from agent_api.platform import telemetry
from agent_api.platform.retry import RetryPolicy
from agent_api.services.ask_agent import AskAgentService

SECRET_USER = "SECRET_USER_TEXT"
SECRET_ARG = "SECRET_TOOL_ARG"
SECRET_RESULT = "SECRET_TOOL_RESULT"
SECRET_SYSTEM = "SYSTEM_SENTINEL_PROMPT"
_SENTINELS = (SECRET_USER, SECRET_ARG, SECRET_RESULT, SECRET_SYSTEM)
_REDACTED = "__REDACTED__"


# --- scripted fake model + tool (same shape as the no-leak suite) --------


class _FakeChat(BaseChatModel):
    """One tool call, then a final answer carrying usage metadata."""

    def __init__(self):
        super().__init__()
        self._calls = 0

    @property
    def _llm_type(self):
        return "fake"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._calls += 1
        if self._calls % 2 == 1:
            msg = AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "probe_tool",
                        "args": {"secret_arg": SECRET_ARG},
                    }
                ],
            )
        else:
            msg = AIMessage(
                content=f"final {SECRET_USER} echo",
                usage_metadata={
                    "input_tokens": 11,
                    "output_tokens": 7,
                    "total_tokens": 18,
                },
            )
        return ChatResult(generations=[ChatGeneration(message=msg)])


@tool
def probe_tool(secret_arg: str) -> str:
    """Probe tool that returns a sentinel result."""
    return SECRET_RESULT


class _ProbeGateway(LangGraphGatewayBase):
    def _build_chat_model(self):
        return _FakeChat()

    def _provider_name(self):
        return "probe"


def _gateway():
    return _ProbeGateway(
        tools=[probe_tool],
        system_prompt=SECRET_SYSTEM,
        retry_policy=RetryPolicy(max_attempts=1),
    )


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def traces():
    """Explicit provider + exporter; LangChain instrumented against it.

    The instrumentor is bound to this provider and removed again
    afterwards, so no global OTel state is touched and no spans leak
    into other tests.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry.instrument_langchain(provider)
    yield SimpleNamespace(exporter=exporter, tracer=provider.get_tracer("test"))
    telemetry.uninstrument_langchain()


def _finished(traces):
    return traces.exporter.get_finished_spans()


def _kinds(traces):
    return {
        (s.attributes or {}).get("openinference.span.kind") for s in _finished(traces)
    }


def _assert_no_content_leak(traces):
    for span in _finished(traces):
        for key, value in (span.attributes or {}).items():
            text = json.dumps(value, default=str)
            for sentinel in _SENTINELS:
                assert sentinel not in text, (
                    f"sentinel {sentinel!r} leaked into span {span.name!r} "
                    f"attribute {key!r}"
                )
        for key in ("input.value", "output.value"):
            if key in (span.attributes or {}):
                assert span.attributes[key] == _REDACTED, (
                    f"span {span.name!r} {key} is not the redaction marker: "
                    f"{span.attributes[key]!r}"
                )


# --- detail spans -----------------------------------------------------------


def test_generate_emits_langgraph_detail_spans(traces):
    turn = _gateway().generate(
        Conversation(
            id="t",
            turns=[Turn(role=USER, content=f"{SECRET_USER} please")],
        )
    )
    assert SECRET_RESULT in turn.tool_results[0].result

    kinds = _kinds(traces)
    # The agent run surfaces as a CHAIN span named "LangGraph" (the
    # instrumentor marks a span AGENT only when "agent" appears in the
    # run name, and "langgraph" does not contain it).
    assert {"CHAIN", "LLM", "TOOL"} <= kinds, kinds
    roots = [s for s in _finished(traces) if s.name == "LangGraph"]
    assert len(roots) == 1 and roots[0].parent is None
    tool_spans = [
        s
        for s in _finished(traces)
        if (s.attributes or {}).get("openinference.span.kind") == "TOOL"
    ]
    assert [s.name for s in tool_spans] == ["probe_tool"]
    _assert_no_content_leak(traces)


def test_generate_records_real_token_counts(traces):
    _gateway().generate(
        Conversation(
            id="t",
            turns=[Turn(role=USER, content="hi")],
        )
    )
    llm_spans = [
        s
        for s in _finished(traces)
        if (s.attributes or {}).get("openinference.span.kind") == "LLM"
    ]
    assert llm_spans, "expected an LLM span"
    counted = [
        s
        for s in llm_spans
        if (s.attributes or {}).get("llm.token_count.prompt") == 11
    ]
    assert len(counted) == 1, [
        (s.name, dict(s.attributes or {})) for s in llm_spans
    ]
    attrs = counted[0].attributes
    assert attrs["llm.token_count.completion"] == 7
    assert attrs["llm.token_count.total"] == 18


def test_stream_emits_langgraph_detail_spans(traces):
    async def collect():
        gateway = _gateway()
        conv = Conversation(
            id="t",
            turns=[Turn(role=USER, content=f"{SECRET_USER} please")],
        )
        return [c async for c in gateway.stream(conv)]

    chunks = asyncio.run(collect())
    # The scripted fake model answers whole-message (no _stream), so
    # astream_events yields tool chunks but no text chunks — that is
    # LangChain behavior for non-streaming models, not a gap here.
    # The point of this test is the spans, asserted below.
    assert any(c.kind == "tool" and c.payload == "probe_tool" for c in chunks)

    kinds = _kinds(traces)
    assert {"LLM", "TOOL"} <= kinds, kinds
    assert any(s.name == "LangGraph" for s in _finished(traces))
    _assert_no_content_leak(traces)


def test_detail_spans_share_one_trace(traces):
    _gateway().generate(
        Conversation(
            id="t",
            turns=[Turn(role=USER, content="hi")],
        )
    )
    spans = _finished(traces)
    assert spans, "expected spans"
    trace_ids = {s.context.trace_id for s in spans}
    assert len(trace_ids) == 1, trace_ids


def test_reinstrumentation_rebinds_provider():
    """A repeat instrument() call must rebind, not keep the old provider.

    The instrumentor is process-global and a fresh instance's
    instrument() is silently ignored (verified empirically, ADR 0010);
    the singleton + uninstrument-first design in telemetry.py exists
    for exactly this. Two turns, two providers: each turn's spans land
    only in its own exporter.
    """
    first = InMemorySpanExporter()
    provider_a = TracerProvider()
    provider_a.add_span_processor(SimpleSpanProcessor(first))
    telemetry.instrument_langchain(provider_a)
    try:
        _gateway().generate(
            Conversation(id="t", turns=[Turn(role=USER, content="hi")])
        )
        first_count = len(first.get_finished_spans())
        assert first_count > 0

        second = InMemorySpanExporter()
        provider_b = TracerProvider()
        provider_b.add_span_processor(SimpleSpanProcessor(second))
        telemetry.instrument_langchain(provider_b)
        _gateway().generate(
            Conversation(id="t", turns=[Turn(role=USER, content="hi")])
        )
        assert len(first.get_finished_spans()) == first_count, (
            "second turn leaked spans into the first provider"
        )
        assert len(second.get_finished_spans()) == first_count
    finally:
        telemetry.uninstrument_langchain()


# --- nesting under the service turn span ------------------------------------


class _FakeRepository:
    def __init__(self):
        self.store: dict[str, Conversation] = {}

    def load(self, conversation_id):
        return self.store.get(conversation_id, Conversation(id=conversation_id))

    def save(self, conversation):
        self.store[conversation.id] = conversation

    def delete(self, conversation_id):
        self.store.pop(conversation_id, None)


class _FakeResolver:
    def __init__(self, gateway):
        self._gateway = gateway

    @property
    def available_keys(self):
        return ("default",)

    def gateway_for(self, key):
        return self._gateway

    def describe(self, key):
        return ("probe", "fake")


def test_detail_spans_nest_under_agent_turn(traces, monkeypatch):
    monkeypatch.setattr("agent_api.services.ask_agent._TRACER", traces.tracer)
    service = AskAgentService(
        conversations=_FakeRepository(),
        gateway_resolver=_FakeResolver(_gateway()),
    )
    service.ask("s1", f"{SECRET_USER} please")

    spans = _finished(traces)
    (turn_span,) = [s for s in spans if s.name == "agent.turn"]
    (root_span,) = [s for s in spans if s.name == "LangGraph"]
    assert root_span.parent is not None
    assert root_span.parent.span_id == turn_span.context.span_id
    assert root_span.context.trace_id == turn_span.context.trace_id
    _assert_no_content_leak(traces)
