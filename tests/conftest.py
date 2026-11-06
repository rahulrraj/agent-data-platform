"""Shared test setup.

ADR 0008 made startup fail-closed: Settings.from_env() refuses to
build an app with neither AUTH_ISSUER nor the explicit dev opt-out.
Tests are not deployments, so the default here is the explicit dev
opt-out (AUTH_DISABLED=true) with no issuer configured — tests that
exercise auth set the AUTH_* variables themselves with monkeypatch,
which overrides this fixture. Do not weaken the fail-closed rule in
settings.py to accommodate tests; set the variables here instead.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _auth_disabled_by_default(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    for var in ("AUTH_ISSUER", "AUTH_AUDIENCE", "AUTH_JWKS_URL"):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture
def otel_global_state():
    """Isolate tests that install a process-global OTel tracer provider.

    The OTel API allows setting the global provider only once per
    process, and Strands caches its Tracer singleton on first use, so
    a test that installs a real provider (or constructs a Strands
    Tracer under a particular OTEL_SEMCONV_STABILITY_OPT_IN value)
    would otherwise leak into every later test in the process. This
    fixture restores the pristine state afterwards. It pokes OTel and
    Strands privates — test-only, never production code.
    """
    import opentelemetry.trace as trace_api
    from opentelemetry.util._once import Once

    import strands.telemetry.tracer as strands_tracer

    yield
    # Shut the provider down first: its batch thread would otherwise
    # keep retrying exports against a dead endpoint from later tests.
    provider = trace_api._TRACER_PROVIDER  # noqa: SLF001 — test-only reset
    shutdown = getattr(provider, "shutdown", None)
    if callable(shutdown):
        try:
            shutdown()
        except Exception:  # noqa: BLE001, S110 — teardown hygiene only
            pass
    trace_api._TRACER_PROVIDER = None  # noqa: SLF001 — test-only reset
    trace_api._TRACER_PROVIDER_SET_ONCE = Once()  # noqa: SLF001
    strands_tracer._tracer_instance = None  # noqa: SLF001
    # HTTPX instrumentation is process-global and binds the provider
    # present at instrument time; without this, TestClient (httpx)
    # requests in later tests would land spans in the dead provider
    # above. The next setup_telemetry call re-instruments fresh.
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().uninstrument()
    except Exception:  # noqa: BLE001, S110 — teardown hygiene only
        pass
    # LangChain instrumentation binds one provider process-globally;
    # without this, spans from later tests would land in the dead
    # provider above. The next setup call re-instruments fresh.
    try:
        from agent_api.platform.telemetry import uninstrument_langchain

        uninstrument_langchain()
    except Exception:  # noqa: BLE001, S110 — teardown hygiene only
        pass
    # Meter provider: the same once-only global rule as the tracer. A
    # test that installs a meter provider (real or test) would
    # otherwise leak into later tests. The instrument cache in
    # telemetry.py must go too, or later records would bind to the
    # dead provider's instruments.
    try:
        import opentelemetry.metrics._internal as metrics_api

        meter_provider = metrics_api._METER_PROVIDER  # noqa: SLF001
        shutdown = getattr(meter_provider, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:  # noqa: BLE001, S110 — teardown hygiene only
                pass
        metrics_api._METER_PROVIDER = None  # noqa: SLF001
        metrics_api._METER_PROVIDER_SET_ONCE = Once()  # noqa: SLF001
    except Exception:  # noqa: BLE001, S110 — teardown hygiene only
        pass
    try:
        from agent_api.platform import telemetry as _telemetry

        _telemetry._METRIC_INSTRUMENTS = None  # noqa: SLF001
    except Exception:  # noqa: BLE001, S110 — teardown hygiene only
        pass
