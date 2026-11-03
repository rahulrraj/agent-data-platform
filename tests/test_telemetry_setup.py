"""Telemetry setup rules (ADR 0009).

Covers the decisions owned by platform/telemetry.py: telemetry is a
strict no-op without an OTLP endpoint, Lambda gets a synchronous
span processor, content redaction is forced even when telemetry is
off, and a gRPC protocol request is refused rather than silently
mis-exported. No backend is involved anywhere here.
"""

from __future__ import annotations

import logging
import os

import pytest
from opentelemetry import trace as trace_api
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent_api.platform import telemetry

_ENDPOINT_VARS = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
)


@pytest.fixture(autouse=True)
def _no_endpoint(monkeypatch):
    for var in _ENDPOINT_VARS:
        monkeypatch.delenv(var, raising=False)
    yield


def _processors(provider: TracerProvider) -> list:
    # Test-only poke at SDK internals: the processor list is the only
    # observable difference between the Lambda and server rules.
    return list(provider._active_span_processor._span_processors)  # noqa: SLF001


# --- endpoint detection ---------------------------------------------------


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, False),
        ({"OTEL_EXPORTER_OTLP_ENDPOINT": ""}, False),
        ({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318"}, True),
        ({"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://localhost:4318/v1/traces"}, True),
    ],
)
def test_otlp_endpoint_configured(monkeypatch, env, expected):
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    assert telemetry.otlp_endpoint_configured() is expected


def test_setup_without_endpoint_is_noop(monkeypatch, otel_global_state):
    """No provider installed, no exporter built, startup unaffected."""
    assert telemetry.setup_telemetry(lambda_runtime=False) is None
    assert trace_api._TRACER_PROVIDER is None  # noqa: SLF001


def test_setup_without_endpoint_lambda_is_noop(otel_global_state):
    assert telemetry.setup_telemetry(lambda_runtime=True) is None
    assert trace_api._TRACER_PROVIDER is None  # noqa: SLF001


# --- processor selection --------------------------------------------------


def test_build_span_processor_lambda_is_simple():
    exporter = object()
    processor = telemetry.build_span_processor(
        lambda_runtime=True, exporter=exporter
    )
    assert isinstance(processor, SimpleSpanProcessor)


def test_build_span_processor_server_is_batch():
    exporter = object()
    processor = telemetry.build_span_processor(
        lambda_runtime=False, exporter=exporter
    )
    assert isinstance(processor, BatchSpanProcessor)


def test_build_span_processor_without_exporter_is_none():
    assert (
        telemetry.build_span_processor(lambda_runtime=True, exporter=None) is None
    )
    assert (
        telemetry.build_span_processor(lambda_runtime=False, exporter=None)
        is None
    )


def test_provider_uses_simple_processor_on_lambda(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    provider = telemetry._build_tracer_provider(lambda_runtime=True)  # noqa: SLF001
    assert provider is not None
    assert any(isinstance(p, SimpleSpanProcessor) for p in _processors(provider))
    assert not any(isinstance(p, BatchSpanProcessor) for p in _processors(provider))


def test_provider_uses_batch_processor_off_lambda(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    provider = telemetry._build_tracer_provider(lambda_runtime=False)  # noqa: SLF001
    assert provider is not None
    assert any(isinstance(p, BatchSpanProcessor) for p in _processors(provider))
    assert not any(isinstance(p, SimpleSpanProcessor) for p in _processors(provider))


# --- gRPC refusal ---------------------------------------------------------


@pytest.mark.parametrize(
    "var", ["OTEL_EXPORTER_OTLP_PROTOCOL", "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL"]
)
def test_grpc_protocol_stays_off(monkeypatch, caplog, var, otel_global_state):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setenv(var, "grpc")
    with caplog.at_level(logging.WARNING):
        assert telemetry.setup_telemetry(lambda_runtime=False) is None
    assert trace_api._TRACER_PROVIDER is None  # noqa: SLF001
    assert "gRPC" in caplog.text


def test_http_protocol_is_accepted(monkeypatch, otel_global_state):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    assert telemetry.setup_telemetry(lambda_runtime=False) is not None


# --- forced content redaction ---------------------------------------------


def test_enforce_redaction_sets_empty_allowlist(monkeypatch):
    monkeypatch.delenv("OTEL_SEMCONV_STABILITY_OPT_IN", raising=False)
    telemetry.enforce_content_redaction()
    assert (
        os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"]
        == "gen_ai_unredacted_attributes="
    )


def test_enforce_redaction_preserves_other_tokens(monkeypatch):
    monkeypatch.setenv(
        "OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental"
    )
    telemetry.enforce_content_redaction()
    assert (
        os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"]
        == "gen_ai_latest_experimental,gen_ai_unredacted_attributes="
    )


def test_enforce_redaction_overrides_operator_allowlist(monkeypatch):
    """An operator allowlist must not re-enable content capture: the
    empty-allowlist policy is a platform rule (PII), not a preference."""
    monkeypatch.setenv(
        "OTEL_SEMCONV_STABILITY_OPT_IN",
        "gen_ai_unredacted_attributes=gen_ai.input.messages",
    )
    telemetry.enforce_content_redaction()
    assert (
        os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"]
        == "gen_ai_unredacted_attributes="
    )


def test_enforce_redaction_is_idempotent(monkeypatch):
    monkeypatch.setenv(
        "OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_unredacted_attributes="
    )
    telemetry.enforce_content_redaction()
    telemetry.enforce_content_redaction()
    assert (
        os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"]
        == "gen_ai_unredacted_attributes="
    )


def test_setup_enforces_redaction_even_without_endpoint(monkeypatch):
    """Redaction is a policy, not an export setting: it holds for any
    exporter added later, so setup enforces it even when off."""
    monkeypatch.setenv(
        "OTEL_SEMCONV_STABILITY_OPT_IN",
        "gen_ai_unredacted_attributes=gen_ai.output.messages",
    )
    assert telemetry.setup_telemetry(lambda_runtime=False) is None
    assert (
        os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"]
        == "gen_ai_unredacted_attributes="
    )


# --- flush ----------------------------------------------------------------


def test_flush_is_noop_when_telemetry_off(otel_global_state):
    telemetry.flush_telemetry()  # must not raise


def test_flush_pushes_batched_spans(otel_global_state):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace_api.set_tracer_provider(provider)
    tracer = trace_api.get_tracer("test")
    with tracer.start_as_current_span("probe"):
        pass
    # Batch processor has not flushed yet on its own schedule.
    assert len(exporter.get_finished_spans()) == 0
    telemetry.flush_telemetry()
    assert [s.name for s in exporter.get_finished_spans()] == ["probe"]


# --- httpx URL scrubbing ---------------------------------------------------


class _RecordingSpan:
    def __init__(self):
        self.attributes: dict = {}

    def set_attribute(self, key, value):
        self.attributes[key] = value


class _RequestInfo:
    def __init__(self, url):
        self.url = url


def test_scrub_url_drops_query_and_fragment():
    span = _RecordingSpan()
    telemetry._scrub_url(  # noqa: SLF001
        span,
        _RequestInfo("https://api.example.com/v1/orders?customer_id=C-123#frag"),
    )
    assert span.attributes["http.url"] == "https://api.example.com/v1/orders"
    assert span.attributes["url.full"] == "https://api.example.com/v1/orders"


def test_scrub_url_accepts_bytes_url():
    span = _RecordingSpan()
    telemetry._scrub_url(  # noqa: SLF001
        span, _RequestInfo(b"https://api.example.com/v1/x?token=abc")
    )
    assert span.attributes["http.url"] == "https://api.example.com/v1/x"
