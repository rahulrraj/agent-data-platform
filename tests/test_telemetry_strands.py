"""Empirical checks against the installed Strands 1.57.1 (ADR 0009).

These tests pin what Strands actually emits — span names, usage
attributes, and the content-redaction default — by running a real
Agent against a stub Model. They exist because the 2026-10-25 review
could only cite index evidence for Strands' span behavior; if a
Strands upgrade changes any of this, these tests go red first.

Two findings are recorded here as tests:

1. DEFAULT: with no `gen_ai_unredacted_attributes=` token in
   OTEL_SEMCONV_STABILITY_OPT_IN, Strands emits prompt/completion
   content UNREDACTED in span events. Content-off therefore has to
   be enforced (platform/telemetry.enforce_content_redaction), not
   assumed.
2. FORCED: with the token present and its allowlist empty, the same
   run emits "[REDACTED]" for every content-bearing field, while
   usage attributes survive.
"""

from __future__ import annotations

import pytest
from opentelemetry import trace as trace_api
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from strands import Agent
from strands.models.model import Model

import strands.telemetry.tracer as strands_tracer

PROMPT_MARKER = "PROMPT-MARKER-7f3a"
REPLY_MARKER = "REPLY-MARKER-9c1e"


class StubModel(Model):
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
        yield {"contentBlockDelta": {"delta": {"text": REPLY_MARKER}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
                "metrics": {"latencyMs": 3},
            }
        }


@pytest.fixture
def strands_provider(monkeypatch, otel_global_state):
    """Explicit provider installed globally; returns the exporter.

    The Strands Tracer singleton binds the global provider at
    construction, so the provider must be installed BEFORE the Agent
    is built, and the singleton reset so it re-reads the env variant
    under test.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace_api.set_tracer_provider(provider)
    strands_tracer._tracer_instance = None  # noqa: SLF001 — test-only reset
    return exporter


def _run_agent():
    agent = Agent(
        model=StubModel(),
        system_prompt="You are a test agent.",
        tools=[],
        callback_handler=lambda **kwargs: None,
    )
    return agent(PROMPT_MARKER)


def _all_span_text(span):
    blob = repr(span.attributes)
    for event in span.events:
        blob += repr(event.attributes)
    return blob


def _spans_by_name(exporter, name):
    return [s for s in exporter.get_finished_spans() if s.name == name]


def _invoke_spans(exporter):
    # The invoke_agent span carries the agent name as a suffix
    # ("invoke_agent Strands Agents" for the default agent name).
    return [
        s
        for s in exporter.get_finished_spans()
        if s.name.startswith("invoke_agent")
    ]


def test_strands_default_emits_content_unredacted(monkeypatch, strands_provider):
    """No opt-in token -> content is emitted raw (the default to fix)."""
    monkeypatch.delenv("OTEL_SEMCONV_STABILITY_OPT_IN", raising=False)
    strands_tracer._tracer_instance = None  # noqa: SLF001
    _run_agent()
    spans = strands_provider.get_finished_spans()
    names = {s.name for s in spans}
    assert any(n.startswith("invoke_agent") for n in names)
    assert "chat" in names
    chat_spans = _spans_by_name(strands_provider, "chat")
    assert any(PROMPT_MARKER in _all_span_text(s) for s in chat_spans), (
        "default Strands run should carry the prompt in a chat-span event; "
        "if this fails, the default changed — re-check the ADR 0009 finding"
    )


def test_strands_forced_redaction_redacts_all_content(monkeypatch, strands_provider):
    """Empty allowlist -> every content field is [REDACTED], usage stays."""
    monkeypatch.setenv(
        "OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_unredacted_attributes="
    )
    strands_tracer._tracer_instance = None  # noqa: SLF001
    _run_agent()
    spans = strands_provider.get_finished_spans()
    assert spans, "expected Strands spans from the stub-model run"
    for span in spans:
        blob = _all_span_text(span)
        assert PROMPT_MARKER not in blob
        assert REPLY_MARKER not in blob
    assert any("[REDACTED]" in _all_span_text(s) for s in spans), (
        "expected at least one redacted content field"
    )
    invoke = _invoke_spans(strands_provider)
    assert invoke, "expected an invoke_agent span"
    assert invoke[0].attributes["gen_ai.usage.input_tokens"] == 10
    assert invoke[0].attributes["gen_ai.usage.output_tokens"] == 5
    assert invoke[0].attributes["gen_ai.usage.total_tokens"] == 15


def test_strands_usage_attributes_present_by_default(monkeypatch, strands_provider):
    """Usage comes from the model response — real numbers, never invented."""
    monkeypatch.delenv("OTEL_SEMCONV_STABILITY_OPT_IN", raising=False)
    strands_tracer._tracer_instance = None  # noqa: SLF001
    _run_agent()
    invoke = _invoke_spans(strands_provider)
    assert invoke
    attrs = invoke[0].attributes
    assert attrs["gen_ai.usage.input_tokens"] == 10
    assert attrs["gen_ai.usage.output_tokens"] == 5
    assert attrs["gen_ai.usage.total_tokens"] == 15


def test_strands_spans_nest_under_ambient_parent(monkeypatch, strands_provider):
    """Strands spans parent to the ambient context — the service turn
    span becomes their parent in production, with no Strands config."""
    monkeypatch.setenv(
        "OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_unredacted_attributes="
    )
    strands_tracer._tracer_instance = None  # noqa: SLF001
    tracer = trace_api.get_tracer("test-parent")
    with tracer.start_as_current_span("agent.turn") as parent:
        _run_agent()
    invoke = _invoke_spans(strands_provider)
    assert invoke
    assert invoke[0].parent is not None
    assert invoke[0].parent.span_id == parent.get_span_context().span_id
