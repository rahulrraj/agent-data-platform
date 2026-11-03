"""Span content for the spans this service adds itself (ADR 0009).

The service turn span and the capability spans are asserted against an
explicit in-memory provider (module tracers monkeypatched to it), so
these tests never touch global OTel state. Strands' own spans are
covered empirically in test_telemetry_strands.py; the live-export
path is covered in test_telemetry_smoke.py.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from agent_api.domain.capability import Capability, ParameterSpec
from agent_api.domain.conversation import ASSISTANT, Conversation, Turn
from agent_api.ports.capability_executor import CapabilityExecutorError
from agent_api.ports.model_gateway import ModelGatewayError, StreamChunk
from agent_api.services.ask_agent import (
    AgentServiceError,
    AskAgentService,
)
from agent_api.tools import ToolDeps


@pytest.fixture
def spans(monkeypatch):
    """An explicit provider + exporter, wired into the module tracers.

    Returns the exporter; finished spans are read from it. The service
    and tool modules bind their tracers at import time, so the
    monkeypatch targets the module-level tracer objects directly —
    no global provider is installed.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    monkeypatch.setattr("agent_api.services.ask_agent._TRACER", tracer)
    monkeypatch.setattr(
        "agent_api.tools.discover_capability.tool._TRACER", tracer
    )
    monkeypatch.setattr(
        "agent_api.tools.execute_capability.tool._TRACER", tracer
    )
    return exporter


def _finished(exporter):
    return exporter.get_finished_spans()


def _by_name(exporter, name):
    return [s for s in _finished(exporter) if s.name == name]


# --- fake ports (same shapes as tests/services/test_ask_agent.py) ---------


class FakeGateway:
    def __init__(self, reply="canned reply", tools=("calculate",)):
        self.reply = reply
        self.tools = tools
        self.fail = False

    def generate(self, conversation):
        if self.fail:
            raise ModelGatewayError("boom")
        return Turn(role=ASSISTANT, content=self.reply, tool_uses=self.tools)

    async def stream(self, conversation):
        if self.fail:
            raise ModelGatewayError("boom")
        yield StreamChunk(kind="text", payload=self.reply)
        for tool in self.tools:
            yield StreamChunk(kind="tool", payload=tool)

    def ping(self):
        pass


class FakeRepository:
    def __init__(self):
        self.store: dict[str, Conversation] = {}

    def load(self, conversation_id):
        return self.store.get(conversation_id, Conversation(id=conversation_id))

    def save(self, conversation):
        self.store[conversation.id] = conversation

    def delete(self, conversation_id):
        self.store.pop(conversation_id, None)


class FakeResolver:
    """Resolver WITH describe() (ADR 0009) — the composition factory."""

    def __init__(self, gateway):
        self._gateway = gateway

    @property
    def available_keys(self):
        return ("default", "fast")

    def gateway_for(self, key):
        if key not in (None, "default", "fast"):
            raise KeyError(key)
        return self._gateway

    def describe(self, key):
        if key in (None, "default"):
            return ("ollama", "qwen2.5:1.5b")
        if key == "fast":
            return ("gemini", "gemini-flash-latest")
        return None


class LegacyResolver:
    """Resolver WITHOUT describe() — duck-typed fakes that predate it."""

    def __init__(self, gateway):
        self._gateway = gateway

    @property
    def available_keys(self):
        return ("default", "fast")

    def gateway_for(self, key):
        if key not in (None, "default", "fast"):
            raise KeyError(key)
        return self._gateway


def _service(gateway=None, resolver=None):
    gateway = gateway or FakeGateway()
    if resolver == "legacy":
        resolver = LegacyResolver(gateway)
    elif resolver == "full":
        resolver = FakeResolver(gateway)
    return AskAgentService(
        conversations=FakeRepository(),
        gateway=gateway if resolver is None else None,
        gateway_resolver=resolver,
    )


# --- service turn spans ----------------------------------------------------


def test_ask_turn_span_attributes(spans):
    service = _service()
    service.ask("s1", "hello")
    (span,) = _by_name(spans, "agent.turn")
    assert span.attributes["agent.session.id"] == "s1"
    assert span.attributes["agent.model.key"] == "default"
    assert span.attributes["agent.tools"] == ("calculate",)
    assert span.attributes["agent.tool.count"] == 1
    assert span.attributes["agent.reply.length"] == len("canned reply")
    assert "enduser.id" not in span.attributes
    assert span.status.status_code is StatusCode.UNSET


def test_ask_turn_span_principal_attribution(spans):
    service = _service()
    service.ask("s1", "hello", principal="user-123")
    (span,) = _by_name(spans, "agent.turn")
    assert span.attributes["enduser.id"] == "user-123"


def test_ask_turn_span_model_attribution(spans):
    service = _service(resolver="full")
    service.ask("s1", "hello", model="fast")
    (span,) = _by_name(spans, "agent.turn")
    assert span.attributes["agent.model.key"] == "fast"
    assert span.attributes["agent.model.provider"] == "gemini"
    assert span.attributes["agent.model.id"] == "gemini-flash-latest"


def test_ask_turn_span_default_model_attribution(spans):
    service = _service(resolver="full")
    service.ask("s1", "hello")
    (span,) = _by_name(spans, "agent.turn")
    assert span.attributes["agent.model.provider"] == "ollama"
    assert span.attributes["agent.model.id"] == "qwen2.5:1.5b"


def test_ask_turn_span_without_describe_has_no_model_attrs(spans):
    """A resolver predating describe() yields no provider/model attrs —
    attribution is best-effort and never fails the turn."""
    service = _service(resolver="legacy")
    service.ask("s1", "hello")
    (span,) = _by_name(spans, "agent.turn")
    assert "agent.model.provider" not in span.attributes
    assert "agent.model.id" not in span.attributes


def test_ask_turn_span_error_status(spans):
    gateway = FakeGateway()
    gateway.fail = True
    service = _service(gateway=gateway)
    with pytest.raises(AgentServiceError):
        service.ask("s1", "hello")
    (span,) = _by_name(spans, "agent.turn")
    assert span.status.status_code is StatusCode.ERROR
    assert span.events, "the gateway failure is recorded on the span"


def test_ask_stream_turn_span(spans):
    service = _service()

    async def collect():
        return [c async for c in service.ask_stream("s1", "hello")]

    asyncio.run(collect())
    (span,) = _by_name(spans, "agent.turn")
    assert span.attributes["agent.session.id"] == "s1"
    assert span.attributes["agent.tools"] == ("calculate",)
    assert span.attributes["agent.reply.length"] == len("canned reply")
    assert span.status.status_code is StatusCode.UNSET


def test_ask_stream_turn_span_marks_abort(spans):
    service = _service()

    async def abort_after_first():
        gen = service.ask_stream("s1", "hello")
        await gen.__anext__()
        await gen.aclose()

    asyncio.run(abort_after_first())
    (span,) = _by_name(spans, "agent.turn")
    assert span.attributes["agent.turn.aborted"] is True


def test_turn_span_records_no_content(spans):
    """No prompt text, no reply text, no token anywhere on the span."""
    service = _service()
    service.ask("s1", "secret prompt C-123", principal="user-123")
    (span,) = _by_name(spans, "agent.turn")
    blob = json.dumps(span.attributes, default=str)
    for event in span.events:
        blob += json.dumps(event.attributes, default=str)
    assert "secret prompt" not in blob
    assert "C-123" not in blob
    assert "canned reply" not in blob
    # And no token-usage attributes are invented (the gateway exposes none).
    assert not any("usage" in key for key in span.attributes)


# --- capability.resolve spans ----------------------------------------------


def _capability(cap_id="customer_by_id"):
    return Capability(
        id=cap_id,
        description="Look up a customer's tier by customer ID",
        kind="http",
        connection="api",
        mode="read",
        parameters=(
            ParameterSpec(
                name="customer_id",
                type="string",
                required=True,
                description="Customer ID, e.g. C-123",
            ),
        ),
    )


class FakeRegistry:
    def __init__(self, capabilities):
        self._capabilities = {c.id: c for c in capabilities}

    def list_all(self):
        return list(self._capabilities.values())

    def get(self, capability_id):
        return self._capabilities.get(capability_id)


class FakeEmbeddingGateway:
    """Deterministic vectors: embed() returns the queued vector."""

    def __init__(self, vectors):
        self._vectors = list(vectors)
        self.model_name = "fake-embed"

    @property
    def similarity_threshold(self):
        return 0.60

    def embed(self, texts):
        return [self._vectors.pop(0) for _ in texts]


def _discover_tool(vectors):
    from agent_api.tools.discover_capability.tool import create_tool

    deps = ToolDeps(
        capability_registry=FakeRegistry(
            [
                _capability("customer_by_id"),
                _capability("orders_by_customer"),
            ]
        ),
        embedding_gateway=FakeEmbeddingGateway(vectors),
    )
    return create_tool(deps)


def test_resolve_span_match(spans):
    # Index vectors: one per capability, then the query vector.
    tool = _discover_tool([[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]])
    result = json.loads(tool(need="customer tier please"))
    assert result["id"] == "customer_by_id"
    (span,) = _by_name(spans, "capability.resolve")
    assert span.attributes["capability.matched"] is True
    assert span.attributes["capability.id"] == "customer_by_id"
    assert span.attributes["capability.match_score"] == pytest.approx(1.0)
    assert span.attributes["capability.ambiguous"] is False


def test_resolve_span_ambiguous(spans):
    tool = _discover_tool([[1.0, 0.0], [0.0, 1.0], [0.7071, 0.7071]])
    result = json.loads(tool(need="something for a customer"))
    assert result["ambiguous"] is True
    (span,) = _by_name(spans, "capability.resolve")
    assert span.attributes["capability.matched"] is True
    assert span.attributes["capability.ambiguous"] is True


def test_resolve_span_no_match(spans):
    tool = _discover_tool([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]])
    tool(need="unrelated weather question")
    (span,) = _by_name(spans, "capability.resolve")
    assert span.attributes["capability.matched"] is False
    assert "capability.id" not in span.attributes


def test_resolve_span_records_no_query_text(spans):
    tool = _discover_tool([[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]])
    tool(need="my secret query for customer C-123")
    (span,) = _by_name(spans, "capability.resolve")
    blob = json.dumps(span.attributes, default=str)
    assert "secret query" not in blob
    assert "C-123" not in blob


# --- capability.execute spans ----------------------------------------------


class FakeExecutor:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.seen_params = None

    def execute(self, capability, params):
        self.seen_params = params
        if self.error is not None:
            raise self.error
        return self.result


def _execute_tool(executor):
    from agent_api.tools.execute_capability.tool import create_tool

    deps = ToolDeps(
        capability_registry=FakeRegistry([_capability("customer_by_id")]),
        capability_executors={"http": executor},
        discovered_capability_ids={"customer_by_id"},
    )
    return create_tool(deps)


def test_execute_span_rows_and_truncated(spans):
    executor = FakeExecutor(
        result={"data": [{"a": 1}, {"a": 2}], "truncated": True}
    )
    tool = _execute_tool(executor)
    assert json.loads(tool(capability_id="customer_by_id", parameters={"customer_id": "C-123"}))
    (span,) = _by_name(spans, "capability.execute")
    assert span.attributes["capability.id"] == "customer_by_id"
    assert span.attributes["capability.kind"] == "http"
    assert span.attributes["capability.rows"] == 2
    assert span.attributes["capability.truncated"] is True
    assert span.status.status_code is StatusCode.UNSET


def test_execute_span_scalar_result_is_one_row(spans):
    executor = FakeExecutor(result={"tier": "gold"})
    tool = _execute_tool(executor)
    tool(capability_id="customer_by_id", parameters={"customer_id": "C-123"})
    (span,) = _by_name(spans, "capability.execute")
    assert span.attributes["capability.rows"] == 1
    assert span.attributes["capability.truncated"] is False


def test_execute_span_error_status_keeps_string_contract(spans):
    executor = FakeExecutor(error=CapabilityExecutorError("downstream 500"))
    tool = _execute_tool(executor)
    result = tool(capability_id="customer_by_id", parameters={"customer_id": "C-123"})
    assert result.startswith("Capability failed:")
    (span,) = _by_name(spans, "capability.execute")
    assert span.status.status_code is StatusCode.ERROR


def test_execute_span_records_no_contents_or_params(spans):
    executor = FakeExecutor(
        result={"data": [{"secret": "C-123", "name": "Acme"}]}
    )
    tool = _execute_tool(executor)
    tool(capability_id="customer_by_id", parameters={"customer_id": "C-123"})
    (span,) = _by_name(spans, "capability.execute")
    blob = json.dumps(span.attributes, default=str)
    for event in span.events:
        blob += json.dumps(event.attributes, default=str)
    assert "C-123" not in blob
    assert "Acme" not in blob
    assert "customer_id" not in blob
