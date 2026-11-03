"""Live-export smoke tests (ADR 0009).

The REAL OTLP HTTP exporter against a local stub receiver, plus the
FastAPI/HTTPX instrumentation behavior end to end. No real backend,
no credentials. Protobuf bodies are never decoded: assertions cover
arrival, request path, and content-type only.

The sandbox's proxy variables would divert localhost requests, so
these tests strip them (the same sandbox-only quirk documented in
AGENTS.md for httpx/ollama clients).
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from opentelemetry import trace as trace_api
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from agent_api.platform import telemetry

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


def test_real_otlp_exporter_reaches_stub(stub_receiver, _no_proxy):
    port, received = stub_receiver
    # An explicit endpoint= is used verbatim (the /v1/traces suffix is
    # appended only for env-based resolution) — pass the full signal URL.
    exporter = OTLPSpanExporter(endpoint=f"http://127.0.0.1:{port}/v1/traces")
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("smoke")
    with tracer.start_as_current_span("smoke-span"):
        pass
    provider.force_flush()
    assert received, "no request reached the stub receiver"
    assert any(path == "/v1/traces" for path, _ in received)
    assert any(
        content_type and content_type.startswith("application/x-protobuf")
        for _, content_type in received
    )


def test_setup_telemetry_exports_to_configured_endpoint(
    stub_receiver, _no_proxy, monkeypatch, otel_global_state
):
    """The setup path (env -> exporter) delivers spans to the stub."""
    port, received = stub_receiver
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", f"http://127.0.0.1:{port}")
    provider = telemetry.setup_telemetry(lambda_runtime=True)
    assert provider is not None
    tracer = trace_api.get_tracer("smoke-setup")
    with tracer.start_as_current_span("setup-span"):
        pass
    telemetry.flush_telemetry()
    assert received, "no request reached the stub receiver"
    assert any(path == "/v1/traces" for path, _ in received)


def test_fastapi_excludes_health_and_ready(
    stub_receiver, _no_proxy, monkeypatch, otel_global_state
):
    """Health/readiness requests produce no export; a traced route does.

    Arrival counting only — no protobuf decoding. The BatchSpanProcessor
    is force-flushed between steps so each step's exports are counted
    separately.
    """
    from fastapi.testclient import TestClient
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    from agent_api.composition import build_app

    port, received = stub_receiver
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", f"http://127.0.0.1:{port}")
    app = build_app()
    # The TestClient itself is httpx-based: its client spans would
    # reach the stub and pollute the arrival count. This test's
    # subject is the FastAPI *server* spans, so drop the client-side
    # instrumentation (the scrub test covers httpx separately). The
    # per-app server middleware is unaffected.
    HTTPXClientInstrumentor().uninstrument()
    client = TestClient(app)
    telemetry.flush_telemetry()  # clear any startup noise
    baseline = len(received)

    assert client.get("/api/health").status_code == 200
    client.get("/api/ready")  # 503 when the model is down; still excluded
    telemetry.flush_telemetry()
    assert len(received) == baseline, "health/ready requests must not export spans"

    # Missing session_id -> 422 before any service work, but the request
    # still passes through the instrumented middleware: it must be traced.
    response = client.post("/api/ask", json={"message": "hi"})
    assert response.status_code == 422
    telemetry.flush_telemetry()
    assert len(received) > baseline, "a traced route must produce an export"

    # Shut the batch processor down: its background thread would
    # otherwise keep retrying exports against this test's dead stub
    # from later tests in the process.
    provider = trace_api.get_tracer_provider()
    shutdown = getattr(provider, "shutdown", None)
    if callable(shutdown):
        shutdown()


def test_httpx_spans_scrub_query(stub_receiver, _no_proxy, otel_global_state):
    """HTTPX client spans carry scheme://host/path only — no query,
    no fragment, no identifiers that rode in the URL."""
    import httpx
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    port, _received = stub_receiver
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace_api.set_tracer_provider(provider)
    # The instrumentor binds the global provider present at instrument
    # time, so an earlier setup_telemetry call would leave it pointing
    # at a dead provider. Uninstrument and re-instrument against this
    # test's provider, then restore the uninstrumented state so later
    # build_app calls instrument fresh.
    instrumentor = HTTPXClientInstrumentor()
    instrumentor.uninstrument()
    try:
        instrumentor.instrument(
            tracer_provider=provider,
            request_hook=telemetry._scrub_url,  # noqa: SLF001
            async_request_hook=telemetry._scrub_url_async,  # noqa: SLF001
        )
        with httpx.Client(trust_env=False) as client:
            response = client.get(
                f"http://127.0.0.1:{port}/v1/orders?customer_id=C-123&token=abc"
            )
    finally:
        instrumentor.uninstrument()
    assert response.status_code == 200
    client_spans = [
        s
        for s in exporter.get_finished_spans()
        if s.kind == SpanKind.CLIENT
    ]
    assert client_spans, "expected an HTTPX client span"
    for span in client_spans:
        for key in ("http.url", "url.full"):
            if key in span.attributes:
                assert span.attributes[key] == f"http://127.0.0.1:{port}/v1/orders"
        assert "C-123" not in repr(span.attributes)
        assert "token=abc" not in repr(span.attributes)
