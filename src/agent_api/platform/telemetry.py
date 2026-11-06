"""OpenTelemetry setup and span helpers (ADR 0009).

This module owns the process-global tracer provider: the composition
root calls :func:`setup_telemetry` once, at app build, before any
agent is constructed (Strands reads the global provider when its
tracer is first created, so setup must precede the first gateway
build). Everything else — the service turn span, the capability
spans — uses :func:`get_tracer` and is a silent no-op when telemetry
is off, because no SDK provider is installed in that case.

Design rules, all enforced here rather than left to operator care:

* Endpoint from the standard OTel environment only
  (``OTEL_EXPORTER_OTLP_ENDPOINT`` / ``OTEL_EXPORTER_OTLP_TRACES_ENDPOINT``).
  When neither is set, telemetry is a strict no-op: no provider is
  installed, no exporter is constructed, and no network attempt is
  made to the SDK's localhost default. No backend is chosen here —
  the backend decision is still open (ADR 0009).
* HTTP/protobuf export only. A ``grpc`` protocol request is warned
  about and left unconfigured rather than silently mis-exported.
* Content capture is OFF, explicitly. Strands 1.57.1 emits prompt,
  completion, tool-argument, tool-result, and system-prompt content
  UNREDACTED by default (verified against the installed
  ``strands/telemetry/tracer.py``: redaction applies only when the
  ``gen_ai_unredacted_attributes=`` token is present in
  ``OTEL_SEMCONV_STABILITY_OPT_IN``). Setup therefore forces that
  token with an EMPTY allowlist — every sensitive attribute becomes
  ``"[REDACTED]"`` — and does so even when no endpoint is configured,
  so the policy holds for any exporter added later. An operator
  allowlist is deliberately overridden: content capture is a
  platform policy (PII), not a deployment preference.
* Sampling is the SDK default (100%) unless the standard
  ``OTEL_TRACES_SAMPLER`` variables say otherwise; this module sets
  no sampler of its own.
* Lambda freeze/thaw: on Lambda the exporter uses a
  ``SimpleSpanProcessor`` (synchronous export per span) and the
  Lambda handler force-flushes at the end of every invocation (see
  :func:`flush_telemetry`). Anywhere else the standard batch
  processor is used.
* No secrets in spans, ever. The HTTPX instrumentation hook strips
  query strings and fragments from recorded URLs (the SDK's own
  redaction only covers a fixed list of sensitive parameter names —
  business identifiers in query strings would otherwise leak), and
  the FastAPI instrumentation captures no request/response headers,
  so the bearer token can never land in a span that way either.
* LangGraph detail is OpenInference, content-hidden. The LangGraph
  adapter's turns are LangChain runnables, so the OpenInference
  LangChain instrumentor (ADR 0010) emits the per-step spans
  (agent / chain / LLM / tool) through the same provider. Its
  TraceConfig hides every content family explicitly — inputs,
  outputs, messages, prompts, retrieval documents, invocation
  parameters, tool definitions — so no prompt, completion, tool
  argument, or tool result can reach a span. The flags are set
  explicitly in code, never left to the instrumentor's env-var
  defaults, and instrumentation happens only when a provider was
  built, so the no-endpoint state stays fully untouched.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit, urlunsplit

from opentelemetry.sdk.trace import SpanProcessor

logger = logging.getLogger(__name__)

#: Token that forces Strands' span-content redaction with an empty
#: allowlist (see module docstring). The token is only meaningful as
#: part of OTEL_SEMCONV_STABILITY_OPT_IN.
_REDACTION_TOKEN = "gen_ai_unredacted_attributes="

#: Health/readiness endpoints are ops noise, not turns: excluded from
#: FastAPI tracing. Matched by regex search against the request URL.
_EXCLUDED_URLS = "api/health,api/ready"

#: The service name when OTEL_SERVICE_NAME is unset. The operator's
#: OTEL_SERVICE_NAME / OTEL_RESOURCE_ATTRIBUTES always win (the SDK
#: resource detector takes precedence over the values passed here).
_DEFAULT_SERVICE_NAME = "agent-api"


def enforce_content_redaction() -> None:
    """Force Strands content redaction, regardless of operator env.

    Any ``gen_ai_unredacted_attributes=...`` token already present is
    removed and replaced with the empty-allowlist form, so no prompt,
    completion, tool-argument, tool-result, or system-prompt content
    is ever emitted in spans. Strands reads this variable when its
    tracer is constructed, so this must run before the first Agent is
    built — :func:`setup_telemetry` calls it, and the composition
    root calls setup before any gateway construction.
    """
    raw = os.environ.get("OTEL_SEMCONV_STABILITY_OPT_IN", "")
    tokens = [
        token.strip()
        for token in raw.split(",")
        if token.strip()
        and not token.strip().startswith("gen_ai_unredacted_attributes")
    ]
    tokens.append(_REDACTION_TOKEN)
    os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = ",".join(tokens)


def otlp_endpoint_configured() -> bool:
    """True when the standard OTel endpoint variables name a backend."""
    return bool(
        os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip()
        or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    )


def _grpc_requested() -> bool:
    """True when the operator asked for the gRPC OTLP protocol."""
    for var in ("OTEL_EXPORTER_OTLP_PROTOCOL", "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL"):
        if os.environ.get(var, "").strip().lower() == "grpc":
            return True
    return False


def build_span_processor(*, lambda_runtime: bool, exporter):
    """The span processor for an exporter: immediate on Lambda, batched.

    Pure function of its inputs (the exporter is supplied by the
    caller) so the Lambda rule is unit-testable without a backend.
    Returns None when there is no exporter — the caller then installs
    no provider at all and tracing stays a no-op.
    """
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    if exporter is None:
        return None
    if lambda_runtime:
        # Freeze/thaw: a background batch may never flush before the
        # environment is suspended, so export synchronously per span.
        return SimpleSpanProcessor(exporter)
    return BatchSpanProcessor(exporter)


def _build_tracer_provider(*, lambda_runtime: bool):
    """Build the SDK tracer provider, or None when telemetry is off.

    The OTLP exporter reads its endpoint/headers from the standard
    OTel environment itself; this function only decides whether one
    may be constructed at all.
    """
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    if not otlp_endpoint_configured():
        return None
    if _grpc_requested():
        logger.warning(
            "OTLP gRPC protocol requested but this service exports "
            "HTTP/protobuf only; telemetry stays off"
        )
        return None
    exporter = OTLPSpanExporter()
    processor = build_span_processor(
        lambda_runtime=lambda_runtime, exporter=exporter
    )
    resource = Resource.create({"service.name": _DEFAULT_SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    if processor is not None:
        provider.add_span_processor(processor)
    return provider


def _set_propagators() -> None:
    """Install W3C tracecontext + baggage propagation (as Strands does).

    Called at most once per process: the OTel API ignores repeat
    attempts, and re-setting would only add noise.
    """
    from opentelemetry import propagate
    from opentelemetry.baggage.propagation import W3CBaggagePropagator
    from opentelemetry.propagators.composite import CompositePropagator
    from opentelemetry.trace.propagation.tracecontext import (
        TraceContextTextMapPropagator,
    )

    propagate.set_global_textmap(
        CompositePropagator(
            [W3CBaggagePropagator(), TraceContextTextMapPropagator()]
        )
    )


def _scrub_url(span, request_info) -> None:
    """HTTPX request hook: drop query strings and fragments from URLs.

    The SDK's own URL redaction only covers a fixed list of sensitive
    parameter names, so business identifiers in query strings (e.g. a
    customer id) would otherwise be recorded in span attributes.
    Overwrite both semconv spellings with scheme://host/path only.
    """
    try:
        raw = request_info.url
        url = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        parts = urlsplit(url)
        scrubbed = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:  # noqa: BLE001 — never break a request for telemetry
        return
    span.set_attribute("http.url", scrubbed)
    span.set_attribute("url.full", scrubbed)


async def _scrub_url_async(span, request_info) -> None:
    """Async form of :func:`_scrub_url` for AsyncClient instrumentation."""
    _scrub_url(span, request_info)


def _instrument_httpx() -> None:
    """Instrument httpx clients (capability + JWKS calls join the trace)."""
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument(
        request_hook=_scrub_url, async_request_hook=_scrub_url_async
    )


def _instrument_fastapi(app) -> None:
    """Instrument a FastAPI app, excluding the ops endpoints."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls=_EXCLUDED_URLS,
        http_capture_headers_server_request=[],
        http_capture_headers_server_response=[],
    )


#: The one LangChain instrumentor for the process. OpenInference
#: instrumentation is process-global (it patches LangChain's
#: callback-manager construction) and binds to a single tracer
#: provider: a second instrument() call on a fresh instance is
#: silently ignored and keeps the old provider (verified
#: empirically, ADR 0010). Exactly one instance is therefore kept
#: and re-bound by uninstrumenting first — see
#: :func:`instrument_langchain`.
_langchain_instrumentor = None
_langchain_instrumented = False


def _redacting_trace_config():
    """OpenInference TraceConfig with every content family hidden.

    Every flag is set explicitly to True: left unset, the
    instrumentor would read OPENINFERENCE_HIDE_* env vars and
    default to maximum observability, which would re-enable
    content capture. Content hiding is a platform rule (ADR 0009
    decision 5), not an operator preference.
    """
    from openinference.instrumentation import TraceConfig

    return TraceConfig(
        hide_inputs=True,
        hide_outputs=True,
        hide_input_messages=True,
        hide_output_messages=True,
        hide_input_text=True,
        hide_output_text=True,
        hide_prompts=True,
        hide_retrieval_documents=True,
        hide_llm_invocation_parameters=True,
        hide_llm_tools=True,
    )


def instrument_langchain(tracer_provider) -> None:
    """Instrument LangChain runnables with content hidden (ADR 0010).

    Binds the process-global OpenInference instrumentor to
    `tracer_provider`, uninstrumenting first so a repeat call rebinds
    instead of silently keeping the old provider. Failures are
    logged and swallowed: telemetry detail must never fail startup.
    """
    global _langchain_instrumentor, _langchain_instrumented
    try:
        from openinference.instrumentation.langchain import (
            LangChainInstrumentor,
        )

        if _langchain_instrumentor is None:
            _langchain_instrumentor = LangChainInstrumentor()
        elif _langchain_instrumented:
            _langchain_instrumentor.uninstrument()
            _langchain_instrumented = False
        _langchain_instrumentor.instrument(
            tracer_provider=tracer_provider,
            config=_redacting_trace_config(),
        )
        _langchain_instrumented = True
    except Exception:  # noqa: BLE001 — telemetry must never fail startup
        logger.exception("LangChain instrumentation failed; continuing without it")


def uninstrument_langchain() -> None:
    """Remove the LangChain instrumentation (test teardown).

    No-op when nothing was instrumented. Called by the
    `otel_global_state` test fixture so a provider bound in one test
    cannot leak spans into later tests.
    """
    global _langchain_instrumented
    if _langchain_instrumented and _langchain_instrumentor is not None:
        try:
            _langchain_instrumentor.uninstrument()
        except Exception:  # noqa: BLE001 — teardown hygiene only
            pass
        _langchain_instrumented = False


def setup_telemetry(*, lambda_runtime: bool, app=None):
    """Configure process-global telemetry; returns the provider or None.

    Called once by the composition root at app build, before any
    gateway or agent is constructed. Returns None (telemetry off) when
    no OTLP endpoint is configured or gRPC was requested — the caller
    treats None as "tracing stays a silent no-op" and must not fail
    startup over it. Content redaction is enforced unconditionally
    (see :func:`enforce_content_redaction`).

    Metrics ride the same endpoint decision: when a metrics endpoint
    is configured, a meter provider is installed and the token-usage
    processor is attached to the tracer provider (when one is built).
    Traces and metrics are independent — either may be on while the
    other is off, depending on which endpoint variables are set.
    """
    enforce_content_redaction()
    provider = _build_tracer_provider(lambda_runtime=lambda_runtime)
    meter_provider = _build_meter_provider()
    if meter_provider is not None:
        from opentelemetry import metrics

        metrics.set_meter_provider(meter_provider)
    if provider is None:
        return None
    from opentelemetry import trace

    trace.set_tracer_provider(provider)
    provider.add_span_processor(UsageMetricsSpanProcessor())
    _set_propagators()
    _instrument_httpx()
    instrument_langchain(provider)
    if app is not None:
        _instrument_fastapi(app)
    return provider


def flush_telemetry(timeout_millis: int = 5000) -> None:
    """Force-flush the global providers; safe no-op when telemetry is off.

    The Lambda handler calls this at the end of every invocation so
    buffered spans and metrics cannot be lost to freeze/thaw. Failures
    are logged and swallowed: telemetry must never fail a request.
    """
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        force_flush = getattr(provider, "force_flush", None)
        if callable(force_flush):
            force_flush(timeout_millis)
    except Exception:  # noqa: BLE001 — telemetry must never fail a request
        logger.exception("telemetry flush failed")
    try:
        from opentelemetry import metrics

        meter_provider = metrics.get_meter_provider()
        force_flush = getattr(meter_provider, "force_flush", None)
        if callable(force_flush):
            force_flush(timeout_millis)
    except Exception:  # noqa: BLE001 — telemetry must never fail a request
        logger.exception("telemetry metrics flush failed")


def get_tracer(name: str):
    """Tracer for `name` from the global provider (no-op when off)."""
    from opentelemetry import trace

    return trace.get_tracer(name)


# --- Metrics (OTel Metrics API) -------------------------------------------
#
# Counts and durations for turns and capability calls, plus token
# usage read off framework model spans. Same rules as traces:
# endpoint from the standard OTel environment only, strict no-op
# when unconfigured, HTTP/protobuf only, no backend chosen here.
# Recording never raises and never carries content: metric
# attributes are ids, keys, kinds, and coarse outcomes only — never
# the principal, never message text, never parameter values.
#
# Instruments are created lazily on first use from the global meter
# (see _get_instruments): a proxy-meter instrument created before a
# provider exists drops records made before the provider is set
# (verified empirically), and setup runs before any turn in
# production, so lazy creation binds to the real provider. Tests
# install a test provider first, then clear the cache (see
# tests/test_telemetry_metrics.py).

_METER_NAME = "agent_api.telemetry"

_TURN_COUNT = "agent.turn.count"
_TURN_DURATION = "agent.turn.duration"
_RESOLVE_COUNT = "capability.resolve.count"
_RESOLVE_DURATION = "capability.resolve.duration"
_EXECUTE_COUNT = "capability.execute.count"
_EXECUTE_DURATION = "capability.execute.duration"
_TOKENS = "agent.tokens"

#: Duration histogram buckets (seconds). The SDK defaults are tuned
#: for milliseconds-scale HTTP spans; turn durations run
#: sub-second to minutes, capability calls sub-second to tens of
#: seconds, so explicit boundaries keep the buckets meaningful.
_TURN_DURATION_BUCKETS = (0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)
_CAPABILITY_DURATION_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)

#: Lazily-created instruments, keyed by attribute name below. Reset
#: to None by tests that swap the global meter provider.
_METRIC_INSTRUMENTS = None


def get_meter(name: str):
    """Meter for `name` from the global provider (no-op when off)."""
    from opentelemetry import metrics

    return metrics.get_meter(name)


def metrics_endpoint_configured() -> bool:
    """True when the standard OTel endpoint variables name a backend
    for metrics (signal-specific or generic)."""
    return bool(
        os.environ.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "").strip()
        or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    )


def _grpc_requested_for_metrics() -> bool:
    """True when the operator asked for the gRPC OTLP protocol for metrics."""
    for var in ("OTEL_EXPORTER_OTLP_PROTOCOL", "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL"):
        if os.environ.get(var, "").strip().lower() == "grpc":
            return True
    return False


def _build_meter_provider():
    """Build the SDK meter provider, or None when metrics are off.

    Periodic exporting reader over the OTLP HTTP exporter; the
    exporter reads its endpoint/headers from the standard OTel
    environment itself. On Lambda the reader's background thread may
    never run before freeze — flush_telemetry() force-flushes at the
    end of every invocation, so metrics still leave on the
    invocation boundary.
    """
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    if not metrics_endpoint_configured():
        return None
    if _grpc_requested_for_metrics():
        logger.warning(
            "OTLP gRPC protocol requested but this service exports "
            "HTTP/protobuf only; metrics stay off"
        )
        return None
    reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    resource = Resource.create({"service.name": _DEFAULT_SERVICE_NAME})
    return MeterProvider(metric_readers=[reader], resource=resource)


def _get_instruments() -> dict:
    """The metric instruments, created once from the global meter."""
    global _METRIC_INSTRUMENTS
    if _METRIC_INSTRUMENTS is None:
        meter = get_meter(_METER_NAME)
        _METRIC_INSTRUMENTS = {
            "turn_count": meter.create_counter(
                _TURN_COUNT, unit="1", description="Agent turns completed"
            ),
            "turn_duration": meter.create_histogram(
                _TURN_DURATION,
                unit="s",
                description="Agent turn wall time",
                explicit_bucket_boundaries_advisory=list(_TURN_DURATION_BUCKETS),
            ),
            "resolve_count": meter.create_counter(
                _RESOLVE_COUNT,
                unit="1",
                description="Capability discovery attempts",
            ),
            "resolve_duration": meter.create_histogram(
                _RESOLVE_DURATION,
                unit="s",
                description="Capability discovery wall time",
                explicit_bucket_boundaries_advisory=list(
                    _CAPABILITY_DURATION_BUCKETS
                ),
            ),
            "execute_count": meter.create_counter(
                _EXECUTE_COUNT,
                unit="1",
                description="Capability executions",
            ),
            "execute_duration": meter.create_histogram(
                _EXECUTE_DURATION,
                unit="s",
                description="Capability execution wall time",
                explicit_bucket_boundaries_advisory=list(
                    _CAPABILITY_DURATION_BUCKETS
                ),
            ),
            "tokens": meter.create_counter(
                _TOKENS,
                unit="{token}",
                description="Model tokens consumed (input/output)",
            ),
        }
    return _METRIC_INSTRUMENTS


def record_turn(
    *,
    duration_seconds: float,
    model_key: str,
    outcome: str,
    provider: str | None = None,
    model_id: str | None = None,
) -> None:
    """Record one completed turn: count + duration.

    `outcome` is "ok", "error", or "aborted". Attributes are the
    allowlist model key and (when the resolver described it) the
    provider/model id — never the principal: per-user metrics would
    explode cardinality and leak identity into the backend. Never
    raises: metrics must never fail a turn.
    """
    try:
        instruments = _get_instruments()
        attributes: dict = {
            "agent.model.key": model_key,
            "agent.turn.outcome": outcome,
        }
        if provider is not None:
            attributes["agent.model.provider"] = provider
        if model_id is not None:
            attributes["agent.model.id"] = model_id
        instruments["turn_count"].add(1, attributes)
        instruments["turn_duration"].record(duration_seconds, attributes)
    except Exception:  # noqa: BLE001 — metrics must never fail a turn
        logger.exception("turn metric recording failed")


def record_capability_resolve(*, duration_seconds: float, outcome: str) -> None:
    """Record one discovery attempt: count + duration.

    `outcome` is "match", "ambiguous", "no_match", or "unavailable".
    No capability id: on no_match there is none, and on ambiguous
    there are two — the span carries the ids, the metric counts the
    outcome. Never raises.
    """
    try:
        instruments = _get_instruments()
        attributes = {"capability.resolve.outcome": outcome}
        instruments["resolve_count"].add(1, attributes)
        instruments["resolve_duration"].record(duration_seconds, attributes)
    except Exception:  # noqa: BLE001 — metrics must never fail a turn
        logger.exception("capability-resolve metric recording failed")


def record_capability_execute(
    *,
    duration_seconds: float,
    capability_id: str,
    kind: str,
    outcome: str,
) -> None:
    """Record one executor run: count + duration.

    `outcome` is "ok" or "error" (executor failure). Validation and
    provenance rejections happen before the executor runs and are not
    counted here — the metric mirrors the `capability.execute` span.
    Never raises.
    """
    try:
        instruments = _get_instruments()
        attributes = {
            "capability.id": capability_id,
            "capability.kind": kind,
            "capability.execute.outcome": outcome,
        }
        instruments["execute_count"].add(1, attributes)
        instruments["execute_duration"].record(duration_seconds, attributes)
    except Exception:  # noqa: BLE001 — metrics must never fail a turn
        logger.exception("capability-execute metric recording failed")


def record_tokens(*, input_tokens: int, output_tokens: int) -> None:
    """Record model token usage: one count per direction.

    No model attribute: Strands carries `gen_ai.request.model` on the
    span but OpenInference does not put a model id on its LLM spans,
    so per-model attribution would be asymmetric — the turn counter
    already breaks turns down by model key. Never raises.
    """
    try:
        instruments = _get_instruments()
        counter = instruments["tokens"]
        if input_tokens > 0:
            counter.add(input_tokens, {"agent.token.type": "input"})
        if output_tokens > 0:
            counter.add(output_tokens, {"agent.token.type": "output"})
    except Exception:  # noqa: BLE001 — metrics must never fail a turn
        logger.exception("token metric recording failed")


class UsageMetricsSpanProcessor(SpanProcessor):
    """Turn framework token usage into the `agent.tokens` counter.

    Reads usage off model spans at export time — the only seam that
    sees the real numbers. The gateway result type exposes neither
    usage nor stop reason (ADR 0009 decision 2), and the service
    must not invent them; Strands' own spans carry the real
    `gen_ai.usage.*` values, OpenInference's LLM spans the real
    `llm.token_count.*` values (both pinned by tests).

    Only per-invocation spans are read: Strands' "chat" span (one
    per model call) and OpenInference LLM-kind spans. Strands'
    `invoke_agent` span carries the same run-aggregated values, so
    reading it too would double-count. Redaction does not touch
    usage — Strands redacts content attributes only — but a value
    that is not a plain integer (e.g. a "[REDACTED]" string from a
    future version) is ignored rather than recorded. Never raises:
    a metrics failure must not break span export.
    """

    def on_start(self, span, parent_context=None):
        return None

    def on_end(self, span):
        try:
            attributes = span.attributes or {}
            if span.name == "chat":
                input_tokens = attributes.get("gen_ai.usage.input_tokens")
                output_tokens = attributes.get("gen_ai.usage.output_tokens")
            elif attributes.get("openinference.span.kind") == "LLM":
                input_tokens = attributes.get("llm.token_count.prompt")
                output_tokens = attributes.get("llm.token_count.completion")
            else:
                return None
            input_tokens = (
                input_tokens
                if isinstance(input_tokens, int)
                and not isinstance(input_tokens, bool)
                else 0
            )
            output_tokens = (
                output_tokens
                if isinstance(output_tokens, int)
                and not isinstance(output_tokens, bool)
                else 0
            )
            if input_tokens > 0 or output_tokens > 0:
                record_tokens(
                    input_tokens=input_tokens, output_tokens=output_tokens
                )
        except Exception:  # noqa: BLE001 — metrics must never break export
            logger.exception("usage-metrics span processing failed")
        return None

    def shutdown(self):
        return None

    def force_flush(self, timeout_millis=None):
        return True
