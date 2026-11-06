"""OTel metrics: turn/capability counts + durations, token usage (ADR 0009).

What is pinned here, all offline against an in-memory metric reader:

* record_* helpers are silent no-ops when no meter provider is set;
* one successful turn records agent.turn.count == 1 (outcome ok,
  model key + provider/id attributes) and one duration point; a
  failed turn records outcome error; a validation failure records
  nothing; an aborted stream records outcome aborted;
* discovery records capability.resolve.count per outcome (match /
  ambiguous / no_match / unavailable) with a duration point each;
* execution records capability.execute.count per outcome (ok / error)
  and nothing for validation/provenance rejections;
* the usage processor reads token counts off Strands "chat" spans and
  OpenInference LLM spans into agent.tokens — and does NOT double
  count the run-aggregated values on Strands' invoke_agent span;
* setup_telemetry installs a meter provider that exports to the
  configured OTLP endpoint (/v1/metrics), and stays off when only a
  traces endpoint is set or gRPC is requested;
* flush_telemetry force-flushes the meter provider (Lambda rule).

No live models, no real backend: the stub receiver stands in for one.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from opentelemetry import metrics as metrics_api
from opentelemetry import trace as trace_api
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from strands import Agent
from strands.models.model import Model

import strands.telemetry.tracer as strands_tracer
from agent_api.capability_import.testrun import (
    KeywordEmbeddingGateway,
    _index_text,  # noqa: SLF001 — same indexed-text format as the tool
)
from agent_api.domain.capability import Capability, ParameterSpec
from agent_api.domain.conversation import ASSISTANT, Conversation, Turn
from agent_api.platform import telemetry
from agent_api.ports.capability_executor import CapabilityExecutorError
from agent_api.ports.model_gateway import ModelGatewayError
from agent_api.services.ask_agent import (
    AgentServiceError,
    AskAgentService,
    InvalidMessageError,
)
from agent_api.tools import ToolDeps
from agent_api.tools.discover_capability.tool import create_tool as create_discover
from agent_api.tools.execute_capability.tool import create_tool as create_execute


# --- metric reader helpers --------------------------------------------------


@pytest.fixture
def metrics_state(otel_global_state):
    """Test MeterProvider + in-memory reader, installed globally.

    Depends on otel_global_state, whose teardown shuts the provider
    down and resets the meter globals + the instrument cache.
    """
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics_api.set_meter_provider(provider)
    telemetry._METRIC_INSTRUMENTS = None  # noqa: SLF001 — fresh instruments off the test provider
    yield reader, provider


def _points(reader, name):
    points = []
    data = reader.get_metrics_data()
    for rm in data.resource_metrics or []:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == name:
                    points.extend(metric.data.data_points)
    return points


def _counter_total(reader, name, attributes):
    total = 0
    for dp in _points(reader, name):
        if dict(dp.attributes or {}) == attributes:
            total += dp.value
    return total


def _histogram(reader, name, attributes):
    for dp in _points(reader, name):
        if dict(dp.attributes or {}) == attributes:
            return dp
    return None


# --- service fakes ----------------------------------------------------------


class _FakeGateway:
    def __init__(self, reply="canned reply", tools=("calculate",)):
        self.seen = []
        self.reply = reply
        self.tools = tools
        self.fail = False

    def generate(self, conversation):
        self.seen.append(conversation)
        if self.fail:
            raise ModelGatewayError("boom")
        return Turn(role=ASSISTANT, content=self.reply, tool_uses=self.tools)

    async def stream(self, conversation):
        self.seen.append(conversation)
        if self.fail:
            raise ModelGatewayError("boom")
        yield SimpleNamespace(kind="text", payload=self.reply)
        for tool in self.tools:
            yield SimpleNamespace(kind="tool", payload=tool)

    def ping(self):
        return None


class _FakeRepository:
    def __init__(self):
        self.store = {}

    def load(self, conversation_id):
        return self.store.setdefault(conversation_id, Conversation(id=conversation_id))

    def save(self, conversation):
        self.store[conversation.id] = conversation

    def ping(self):
        return None


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


def _make_service(gateway=None, resolver=False):
    gateway = gateway if gateway is not None else _FakeGateway()
    repo = _FakeRepository()
    if resolver:
        service = AskAgentService(
            conversations=repo, gateway_resolver=_FakeResolver(gateway)
        )
    else:
        service = AskAgentService(gateway=gateway, conversations=repo)
    return service, gateway, repo


# --- record helpers: no-op without a provider -------------------------------


def test_record_functions_do_not_raise_without_provider():
    """With no meter provider installed, recording is a silent no-op."""
    telemetry.record_turn(
        duration_seconds=0.1, model_key="default", outcome="ok"
    )
    telemetry.record_capability_resolve(duration_seconds=0.1, outcome="match")
    telemetry.record_capability_execute(
        duration_seconds=0.1, capability_id="c", kind="http", outcome="ok"
    )
    telemetry.record_tokens(input_tokens=10, output_tokens=5)
    telemetry.UsageMetricsSpanProcessor().on_end(
        SimpleNamespace(
            name="chat",
            attributes={
                "gen_ai.usage.input_tokens": 10,
                "gen_ai.usage.output_tokens": 5,
            },
        )
    )


# --- turn metrics -----------------------------------------------------------


def test_turn_metrics_success_error_and_no_validation_count(metrics_state):
    reader, _ = metrics_state
    service, gateway, _ = _make_service(resolver=True)
    service.ask("s1", "hello")

    ok_attrs = {
        "agent.model.key": "default",
        "agent.turn.outcome": "ok",
        "agent.model.provider": "probe",
        "agent.model.id": "fake",
    }
    assert _counter_total(reader, "agent.turn.count", ok_attrs) == 1
    duration = _histogram(reader, "agent.turn.duration", ok_attrs)
    assert duration is not None and duration.count == 1 and duration.sum > 0

    gateway.fail = True
    with pytest.raises(AgentServiceError):
        service.ask("s1", "hello again")
    err_attrs = dict(ok_attrs, **{"agent.turn.outcome": "error"})
    assert _counter_total(reader, "agent.turn.count", err_attrs) == 1

    # A validation failure runs nothing: no span, no metric.
    with pytest.raises(InvalidMessageError):
        service.ask("s1", "   ")
    assert len(_points(reader, "agent.turn.count")) == 2


def test_turn_metrics_stream_abort(metrics_state):
    reader, _ = metrics_state

    async def run():
        service, _, _ = _make_service()
        gen = service.ask_stream("s1", "hi")
        first = await gen.__anext__()
        await gen.aclose()
        return first

    first = asyncio.run(run())
    assert first.kind == "text"
    assert (
        _counter_total(
            reader,
            "agent.turn.count",
            {"agent.model.key": "default", "agent.turn.outcome": "aborted"},
        )
        == 1
    )


def test_turn_metrics_stream_error(metrics_state):
    reader, _ = metrics_state

    async def run():
        service, gateway, _ = _make_service()
        gateway.fail = True
        with pytest.raises(AgentServiceError):
            [c async for c in service.ask_stream("s1", "hi")]

    asyncio.run(run())
    assert (
        _counter_total(
            reader,
            "agent.turn.count",
            {"agent.model.key": "default", "agent.turn.outcome": "error"},
        )
        == 1
    )


# --- capability metrics -----------------------------------------------------


class _FakeRegistry:
    def __init__(self, caps):
        self._caps = {c.id: c for c in caps}

    def list_all(self):
        return list(self._caps.values())

    def get(self, capability_id):
        return self._caps.get(capability_id)

    def ping(self):
        return None


def _tier_caps():
    # Two capabilities whose indexed texts tokenize identically, so
    # "customer tier" ties them exactly (margin 0 <= 0.05) and the
    # tool returns both candidates instead of forcing one.
    return [
        Capability(
            id="tier_by_id",
            description="customer tier lookup by id",
            kind="http",
            connection="x",
            mode="read",
            parameters=(
                ParameterSpec(
                    name="customer_id",
                    type="string",
                    required=True,
                    description="Customer ID",
                ),
            ),
            spec={},
        ),
        Capability(
            id="tier_by_number",
            description="customer tier lookup",
            kind="http",
            connection="x",
            mode="read",
            parameters=(
                ParameterSpec(
                    name="customer_id",
                    type="string",
                    required=True,
                    description="Customer ID",
                ),
            ),
            spec={},
        ),
    ]


def _discover_with(caps, needs, gateway=None):
    if gateway is None:
        texts = [_index_text(c) for c in caps] + list(needs)
        gateway = KeywordEmbeddingGateway(texts)
    deps = ToolDeps(
        capability_registry=_FakeRegistry(caps),
        embedding_gateway=gateway,
    )
    return create_discover(deps)


def test_resolve_metrics_per_outcome(metrics_state):
    reader, _ = metrics_state
    caps = _tier_caps()

    match = _discover_with(caps[:1], ["customer tier lookup"])
    assert json.loads(match("customer tier lookup"))["id"] == "tier_by_id"

    no_match = _discover_with(caps[:1], ["quantum flux calibration"])
    assert "No data capability matches" in no_match("quantum flux calibration")

    ambiguous = _discover_with(caps, ["customer tier"])
    assert json.loads(ambiguous("customer tier"))["ambiguous"] is True

    assert (
        _counter_total(
            reader,
            "capability.resolve.count",
            {"capability.resolve.outcome": "match"},
        )
        == 1
    )
    assert (
        _counter_total(
            reader,
            "capability.resolve.count",
            {"capability.resolve.outcome": "no_match"},
        )
        == 1
    )
    assert (
        _counter_total(
            reader,
            "capability.resolve.count",
            {"capability.resolve.outcome": "ambiguous"},
        )
        == 1
    )
    for outcome in ("match", "no_match", "ambiguous"):
        point = _histogram(
            reader,
            "capability.resolve.duration",
            {"capability.resolve.outcome": outcome},
        )
        assert point is not None and point.count == 1 and point.sum > 0


def test_resolve_metrics_unavailable(metrics_state):
    reader, _ = metrics_state

    class _FailingEmbeddings:
        model_name = "failing"
        similarity_threshold = 0.60

        def embed(self, texts):
            raise RuntimeError("embedding backend down")

        def ping(self):
            return None

    failing = _discover_with(_tier_caps()[:1], [], gateway=_FailingEmbeddings())
    assert "temporarily unavailable" in failing("customer tier lookup")

    empty = _discover_with([], [])
    assert "No data capabilities are configured" in empty("anything")

    assert (
        _counter_total(
            reader,
            "capability.resolve.count",
            {"capability.resolve.outcome": "unavailable"},
        )
        == 2
    )


class _FakeExecutor:
    kind = "snowflake"

    def execute(self, capability, params):
        return {"data": [{"name": "Acme", "tier": "Gold"}]}


class _FailingExecutor:
    kind = "snowflake"

    def execute(self, capability, params):
        raise CapabilityExecutorError("boom")


def _execute_with(executor):
    cap = Capability(
        id="customer_by_id",
        description="Look up a customer's tier by customer ID",
        kind="snowflake",
        connection="snowflake_analytics",
        mode="read",
        parameters=(
            ParameterSpec(
                name="customer_id",
                type="string",
                required=True,
                description="Customer ID",
            ),
        ),
        spec={},
    )
    deps = ToolDeps(
        capability_registry=_FakeRegistry([cap]),
        capability_executors={"snowflake": executor},
    )
    deps.discovered_capability_ids.add(cap.id)
    return create_execute(deps), cap


def test_execute_metrics_ok_error_and_no_validation_count(metrics_state):
    reader, _ = metrics_state
    execute, _ = _execute_with(_FakeExecutor())
    out = json.loads(execute("customer_by_id", {"customer_id": "C-123"}))
    assert out["data"][0]["tier"] == "Gold"

    ok_attrs = {
        "capability.id": "customer_by_id",
        "capability.kind": "snowflake",
        "capability.execute.outcome": "ok",
    }
    assert _counter_total(reader, "capability.execute.count", ok_attrs) == 1
    duration = _histogram(reader, "capability.execute.duration", ok_attrs)
    assert duration is not None and duration.count == 1 and duration.sum > 0

    failing, _ = _execute_with(_FailingExecutor())
    assert "Capability failed" in failing("customer_by_id", {"customer_id": "C-123"})
    err_attrs = dict(ok_attrs, **{"capability.execute.outcome": "error"})
    assert _counter_total(reader, "capability.execute.count", err_attrs) == 1

    # A validation rejection never reaches the executor: no metric.
    assert "missing required parameter" in execute("customer_by_id", {})
    assert len(_points(reader, "capability.execute.count")) == 2


# --- token usage via span attributes ----------------------------------------


class _StubModel(Model):
    """A Model that answers one canned turn with declared token usage."""

    def __init__(self):
        self._config = {"model_id": "stub-model"}

    def update_config(self, **model_config):
        self._config.update(model_config)

    def get_config(self):
        return dict(self._config)

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise NotImplementedError

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockDelta": {"delta": {"text": "reply"}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
                "metrics": {"latencyMs": 3},
            }
        }


@pytest.fixture
def strands_traces(otel_global_state, monkeypatch):
    """Test tracer provider with the usage processor attached."""
    monkeypatch.setenv(
        "OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_unredacted_attributes="
    )
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    provider.add_span_processor(telemetry.UsageMetricsSpanProcessor())
    trace_api.set_tracer_provider(provider)
    strands_tracer._tracer_instance = None  # noqa: SLF001 — test-only reset
    return exporter


def test_usage_processor_records_strands_tokens_once(
    metrics_state, strands_traces
):
    reader, _ = metrics_state
    agent = Agent(
        model=_StubModel(),
        system_prompt="You are a test agent.",
        tools=[],
        callback_handler=lambda **kwargs: None,
    )
    agent("hello")

    # Exactly one invocation's usage: the "chat" span carries 10/5 and
    # the invoke_agent span carries the same run-aggregated 10/5 — a
    # processor that read both would record 20/10.
    assert (
        _counter_total(
            reader, "agent.tokens", {"agent.token.type": "input"}
        )
        == 10
    )
    assert (
        _counter_total(
            reader, "agent.tokens", {"agent.token.type": "output"}
        )
        == 5
    )
    chat = [
        s for s in strands_traces.get_finished_spans() if s.name == "chat"
    ]
    assert chat, "expected a chat span"
    assert chat[0].attributes["gen_ai.usage.input_tokens"] == 10


def test_usage_processor_rules(metrics_state):
    reader, _ = metrics_state
    processor = telemetry.UsageMetricsSpanProcessor()

    # Strands per-invocation span.
    processor.on_end(
        SimpleNamespace(
            name="chat",
            attributes={
                "gen_ai.usage.input_tokens": 3,
                "gen_ai.usage.output_tokens": 2,
            },
        )
    )
    # The run-aggregated span must NOT be read (double count).
    processor.on_end(
        SimpleNamespace(
            name="invoke_agent Strands Agents",
            attributes={
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 50,
            },
        )
    )
    # OpenInference LLM span.
    processor.on_end(
        SimpleNamespace(
            name="ChatGoogleGenerativeAI",
            attributes={
                "openinference.span.kind": "LLM",
                "llm.token_count.prompt": 11,
                "llm.token_count.completion": 7,
            },
        )
    )
    # LLM span without token counts: nothing to record.
    processor.on_end(
        SimpleNamespace(
            name="ChatGoogleGenerativeAI",
            attributes={"openinference.span.kind": "LLM"},
        )
    )
    # A redacted (non-integer) usage value is ignored, not recorded.
    processor.on_end(
        SimpleNamespace(
            name="chat",
            attributes={
                "gen_ai.usage.input_tokens": "[REDACTED]",
                "gen_ai.usage.output_tokens": "[REDACTED]",
            },
        )
    )
    # A span with no attributes at all: no crash, no record.
    processor.on_end(SimpleNamespace(name="chat", attributes=None))
    # Zero usage: no record.
    processor.on_end(
        SimpleNamespace(
            name="chat",
            attributes={
                "gen_ai.usage.input_tokens": 0,
                "gen_ai.usage.output_tokens": 0,
            },
        )
    )

    assert (
        _counter_total(reader, "agent.tokens", {"agent.token.type": "input"})
        == 14
    )
    assert (
        _counter_total(reader, "agent.tokens", {"agent.token.type": "output"})
        == 9
    )


# --- setup / export / flush ---------------------------------------------------


_PROXY_VARS = (
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "all_proxy",
    "ALL_PROXY",
)


@pytest.fixture
def _no_proxy(monkeypatch):
    for var in _PROXY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    yield


@pytest.fixture
def stub_receiver():
    """A stub OTLP receiver: records (path, content-type) per request."""
    received: list = []

    class Handler(BaseHTTPRequestHandler):
        def _record(self):
            length = int(self.headers.get("Content-Length", 0))
            if length:
                self.rfile.read(length)
            received.append((self.path, self.headers.get("Content-Type")))
            body = b""
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self._record()

        def do_GET(self):
            self._record()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], received
    finally:
        server.shutdown()
        server.server_close()


def test_setup_telemetry_exports_metrics_to_stub(
    stub_receiver, _no_proxy, monkeypatch, otel_global_state
):
    port, received = stub_receiver
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", f"http://127.0.0.1:{port}")
    provider = telemetry.setup_telemetry(lambda_runtime=True)
    assert provider is not None
    telemetry.record_turn(duration_seconds=0.12, model_key="default", outcome="ok")
    telemetry.flush_telemetry()
    assert received, "no request reached the stub receiver"
    assert any(path == "/v1/metrics" for path, _ in received)
    assert any(
        content_type and content_type.startswith("application/x-protobuf")
        for _, content_type in received
    )


def test_metrics_off_when_only_traces_endpoint_set(
    monkeypatch, otel_global_state
):
    """Traces on, metrics off: a traces-only endpoint installs no
    meter provider, and recording stays a no-op."""
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://127.0.0.1:9/v1/traces"
    )
    provider = telemetry.setup_telemetry(lambda_runtime=True)
    assert provider is not None
    assert not isinstance(metrics_api.get_meter_provider(), MeterProvider)
    telemetry.record_turn(duration_seconds=0.1, model_key="default", outcome="ok")


def test_metrics_grpc_protocol_refused(monkeypatch, otel_global_state):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", "grpc")
    provider = telemetry.setup_telemetry(lambda_runtime=True)
    assert provider is not None  # traces still on (traces protocol not grpc)
    assert not isinstance(metrics_api.get_meter_provider(), MeterProvider)


def test_flush_telemetry_flushes_meter_provider(metrics_state, monkeypatch):
    reader, provider = metrics_state
    calls = []
    monkeypatch.setattr(
        provider,
        "force_flush",
        lambda timeout_millis=None: calls.append(timeout_millis) or True,
    )
    telemetry.record_turn(duration_seconds=0.1, model_key="default", outcome="ok")
    telemetry.flush_telemetry()
    assert calls, "flush_telemetry must force-flush the meter provider"


def test_flush_telemetry_is_noop_without_provider():
    telemetry.flush_telemetry()
