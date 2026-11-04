# ADR 0010: LangGraph OTel detail — OpenInference with content hidden

Date: 2026-11-04

Status: **Accepted 2026-11-04 — implemented 2026-11-04** (OpenInference
LangChain instrumentor bound in telemetry setup, content hidden by an
explicit TraceConfig; local only, not pushed or deployed). No backend
is configured; nothing verified against a real collector.

## Contents

- [Context](#context)
- [Decision](#decision)
- [Consequences](#consequences)
- [Open items](#open-items)

## Context

ADR 0009 left LangGraph turns at turn granularity: the service
`agent.turn` span and the capability spans fire for both frameworks,
but only Strands had framework-native detail underneath. The
follow-up named two routes: langsmith[otel] or OpenInference. Both
needed empirical checking against the platform's absolute rule
(ADR 0009 decision 5): no span carries prompts, completions, message
content, row contents, parameter values, or the bearer token —
content capture OFF, enforced, not assumed.

Verified 2026-11-04 against the installed packages
(openinference-instrumentation-langchain 0.1.81,
openinference-instrumentation 0.1.75) and by running a real
LangGraph agent against a stub model with sentinel content in the
user message, tool argument, tool result, and system prompt (pinned
by `tests/test_telemetry_langgraph.py`):

- The instrumentor records input/output values, input/output
  messages, prompts, prompt templates, invocation parameters, tool
  definitions, function calls, retrieval documents, and run
  metadata on spans by default — content ON unless hidden.
- `TraceConfig` has first-class hide flags for every content family
  (`hide_inputs`, `hide_outputs`, `hide_input_messages`,
  `hide_output_messages`, `hide_input_text`, `hide_output_text`,
  `hide_prompts`, `hide_retrieval_documents`,
  `hide_llm_invocation_parameters`, `hide_llm_tools`), applied by
  the instrumentor's `mask()` to every attribute. With all of them
  set explicitly, stub-model turns on both the sync and streaming
  paths emit spans with zero sentinel leakage: `input.value` /
  `output.value` are `"__REDACTED__"`, message and prompt
  attributes are dropped.
- The one content-shaped attribute outside the hide flags,
  `llm.function_call` (legacy function-call shape), does not
  materialize for the modern tool-calling path — tool calls travel
  inside output messages, which are hidden. If a provider ever
  emits it, the sentinel test trips: it asserts no sentinel string
  in ANY span attribute.
- The instrumentor is process-global and binds one provider: a
  second `instrument()` on a fresh instance is silently ignored
  and keeps the old provider (verified empirically). Rebinding
  requires uninstrument-first on the same instance.
- Span shape: the agent run is a CHAIN span named `LangGraph`; node
  runs are CHAIN spans (`model`, `tools`); model calls are LLM
  spans carrying the real `llm.token_count.*` values from usage
  metadata; tool calls are TOOL spans named for the tool. There is
  no AGENT-kind span: the instrumentor marks AGENT only when
  "agent" appears in the run name, and "langgraph" does not
  contain it.

## Decision

1. **OpenInference, configured content-hidden.**
   `platform/telemetry.py` binds `LangChainInstrumentor` to the
   provider built by `setup_telemetry`, with an explicit all-hide
   `TraceConfig` (flags set in code, never left to the
   `OPENINFERENCE_HIDE_*` env-var defaults). Instrumentation
   happens only when a provider was built, so the no-endpoint
   state stays fully untouched. A single module-level instrumentor
   instance is kept and uninstrumented first on repeat calls, so
   rebinding works (tests, rebuilds).
2. **No hand-rolled callback handler.** A callback handler would
   record only whitelisted attributes (redaction by construction)
   with zero new dependencies, but it would emit non-standard span
   semantics no backend recognizes as LLM spans, and it is more
   code to maintain against a manual seam. OpenInference was
   verified content-free with the config above, so the standard
   route wins; the callback route stays the fallback if
   OpenInference ever cannot be configured content-free.
3. **langsmith[otel] not used.** LangSmith tracing exists to
   record content; routing it to OTLP would need the same
   scrubbing with more moving parts (a LangSmith account concept)
   for no additional detail.
4. **Failure is swallowed.** If instrumentation fails (missing
   dependency, version conflict), setup logs and continues:
   telemetry detail must never fail startup.
5. **Tests pin the behavior.** Span shape on both paths, sentinel
   redaction, real token counts, single-trace nesting under
   `agent.turn`, provider rebinding, and setup wiring. The
   sentinel test is the tripwire: any new content attribute in a
   future OpenInference version trips it first.

## Consequences

- LangGraph turns are traceable at the same depth as Strands
  turns: `agent.turn` → `LangGraph` root → `model`/`tools`
  chains → LLM/tool spans → capability spans → HTTPX spans.
- New dependencies, pinned in `pyproject.toml` (no lockfile):
  `openinference-instrumentation-langchain`,
  `openinference-instrumentation` (versions track the verified
  pair).
- The `otel_global_state` fixture also uninstruments LangChain on
  teardown, so a provider bound in one test cannot leak spans into
  later tests.
- Verified against openinference-instrumentation-langchain 0.1.81
  / openinference-instrumentation 0.1.75 and langchain 1.4.6 /
  langgraph 1.2.16 only. An upgrade that changes span names, the
  `TraceConfig` flags, or the instrumentor's binding behavior will
  trip `tests/test_telemetry_langgraph.py` first.

## Open items

- Re-check the content-hiding behavior on every OpenInference
  upgrade (the sentinel test pins the 0.1.81 / 0.1.75 behavior).
- Live verification against whichever OTLP backend the deployment
  uses (no backend is chosen in code, per the backend-agnostic
  direction) should confirm the LangGraph spans arrive redacted
  end to end.
