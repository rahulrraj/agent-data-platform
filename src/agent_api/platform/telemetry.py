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
    """
    enforce_content_redaction()
    provider = _build_tracer_provider(lambda_runtime=lambda_runtime)
    if provider is None:
        return None
    from opentelemetry import trace

    trace.set_tracer_provider(provider)
    _set_propagators()
    _instrument_httpx()
    instrument_langchain(provider)
    if app is not None:
        _instrument_fastapi(app)
    return provider


def flush_telemetry(timeout_millis: int = 5000) -> None:
    """Force-flush the global provider; safe no-op when telemetry is off.

    The Lambda handler calls this at the end of every invocation so
    buffered spans cannot be lost to freeze/thaw. Failures are logged
    and swallowed: telemetry must never fail a request.
    """
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        force_flush = getattr(provider, "force_flush", None)
        if callable(force_flush):
            force_flush(timeout_millis)
    except Exception:  # noqa: BLE001 — telemetry must never fail a request
        logger.exception("telemetry flush failed")


def get_tracer(name: str):
    """Tracer for `name` from the global provider (no-op when off)."""
    from opentelemetry import trace

    return trace.get_tracer(name)
