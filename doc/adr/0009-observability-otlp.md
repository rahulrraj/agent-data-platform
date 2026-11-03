# ADR 0009: Observability — OTLP tracing, Strands-native + service spans, no content

Date: 2026-11-03

Status: **Accepted 2026-11-03 — implemented 2026-11-03** (Strands-native
telemetry via the global provider, service turn spans, capability spans,
FastAPI/HTTPX instrumentation, Lambda flush; local only, not pushed or
deployed). No backend is configured: the backend choice is still open,
and nothing has been verified against a real collector.

## Context

The application logged only. The 2026-10-25 observability review
(research report + page `agent-observability-otlp-review`) recommended
framework-native OpenTelemetry + FastAPI/HTTPX instrumentation →
direct OTLP on Lambda (with a flush, else the freeze/thaw trap loses
spans) / Collector on EKS → Langfuse Cloud or self-hosted Phoenix,
with per-turn and per-capability record specs. Three of that review's
claims needed empirical checking before building on them, because
part of its Strands evidence was index-only:

- What span names/attributes Strands 1.57.1 actually emits.
- Whether Strands records prompt/completion content by default.
- Whether the SDK honors `OTEL_TRACES_SAMPLER` without explicit code.

Verified 2026-11-03 against the installed Strands source
(`strands/telemetry/tracer.py`, `strands/telemetry/config.py`) and by
running a real `Agent` against a stub model (pinned by
`tests/test_telemetry_strands.py`):

- Strands emits `invoke_agent <agent name>`, `chat`, and
  `execute_event_loop_cycle` spans (plus `execute_tool` / `memory.*`
  when tools/memory run), with real `gen_ai.usage.*` token attributes
  taken from the model response — never invented.
- **Content default: Strands emits prompt, completion, tool-argument,
  tool-result, and system-prompt content UNREDACTED by default.**
  Redaction applies only when the token
  `gen_ai_unredacted_attributes=<list>` is present in
  `OTEL_SEMCONV_STABILITY_OPT_IN`, and then only the listed names
  escape redaction (an empty list redacts everything). The variable
  `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` is NOT read by
  Strands 1.57.1 — the semconv opt-in token is the only control. This
  closes the review's open question, against the safe direction:
  content-off must be enforced, not assumed.
- The OTel SDK reads `OTEL_TRACES_SAMPLER` itself (100% is the
  default); no sampler code is needed.
- `StrandsTelemetry.setup_otlp_exporter()` hardcodes a
  `BatchSpanProcessor` with no processor choice, so the Lambda rule
  (below) cannot be expressed through it. The provider is therefore
  constructed directly, and `StrandsTelemetry` is not used.

Rahul approved 2026-11-03: "implement observability — Strands
supports OTLP". The backend choice stays open by his original brief:
no backend, no credentials, no real-backend calls in this change.

## Decision

1. **Strands-native telemetry through the global provider.**
   `platform/telemetry.py` builds an SDK `TracerProvider` (resource
   `service.name`, default `agent-api`, overridable by
   `OTEL_SERVICE_NAME` / `OTEL_RESOURCE_ATTRIBUTES`) with an OTLP
   HTTP/protobuf exporter, installs it as the global provider, and
   sets W3C tracecontext + baggage propagation — the same wiring
   `StrandsTelemetry` would do, minus the parts it gets wrong for
   this service. No Strands config is needed: Strands binds the
   global provider when its tracer is first created, so its spans
   nest under the service turn span with no framework code.
2. **Service turn span, framework-agnostic.** `AskAgentService.ask` /
   `ask_stream` emit one `agent.turn` span per turn (the span stays
   open across `ask_stream` yields; an aborted stream is marked
   `agent.turn.aborted`). Attributes are ids, counts, and lengths
   only: `agent.session.id`, `agent.model.key`, `agent.model.provider`
   / `agent.model.id` (via a new `describe()` method on the
   `ModelGatewayResolver` port — the service never knows the matrix),
   `enduser.id` (the authenticated `sub` from the ADR 0008 request
   context, only when authenticated), `agent.tools`,
   `agent.tool.count`, `agent.reply.length`, error status + exception
   on gateway failure. Token usage and stop reason are deliberately
   NOT recorded here: the gateway result type exposes neither, and
   Strands' own `invoke_agent` span already carries the real
   `gen_ai.usage.*` values and `gen_ai.response.finish_reasons`.
3. **Capability spans.** `capability.resolve` (matched flag,
   capability id, match score, ambiguous flag — never the query text)
   and `capability.execute` (capability id, kind, row count,
   `truncated` flag, error status on executor failure — never row
   contents or parameter values). These live in the two factory
   tools, a deliberate exception to "tool functions stay pure":
   no other seam sees the match score or the row counts.
4. **FastAPI + HTTPX auto-instrumentation.** The composition root
   instruments the app (`excluded_urls="api/health,api/ready"`, no
   request/response headers captured) and httpx globally. The httpx
   request hook overwrites the recorded URL with
   scheme://host/path: the SDK's own redaction covers only a fixed
   list of sensitive parameter names, so business identifiers in
   query strings would otherwise leak into spans.
5. **Content capture OFF, enforced.** Setup rewrites
   `OTEL_SEMCONV_STABILITY_OPT_IN` to contain
   `gen_ai_unredacted_attributes=` with an EMPTY allowlist —
   dropping any operator allowlist — before any `Agent` is
   constructed, and does so even when no endpoint is configured, so
   the policy holds for any exporter added later. This is a platform
   rule (PII), not a deployment preference. No span, event, or log
   in this service carries prompts, completions, message content,
   row contents, parameter values, or the bearer token.
6. **Endpoint from env, no-op when unconfigured.** Spans export to
   wherever `OTEL_EXPORTER_OTLP_ENDPOINT` /
   `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` points (standard OTel env,
   read by the exporter itself; signal-specific headers via the
   standard `OTEL_EXPORTER_OTLP_*_HEADERS` vars). When neither is
   set, no provider is installed, no exporter is constructed, and
   no network attempt is made to the SDK's localhost default —
   telemetry is a silent no-op and startup never fails over it.
   HTTP/protobuf only: a `grpc` protocol request is warned about
   and left unconfigured. **Backend undecided** (Langfuse Cloud vs
   self-hosted Phoenix were the review's candidates) — no backend
   is picked, no credentials exist, no real backend was called.
7. **Sampling: SDK default (100%), via `OTEL_TRACES_SAMPLER`.** No
   sampler is set in code; no tail sampling in this change.
8. **Lambda flush rule.** On Lambda (`AWS_LAMBDA_FUNCTION_NAME` set,
   via `Settings.lambda_runtime`) the provider uses a
   `SimpleSpanProcessor` (synchronous export per span — a batch
   thread may never run again after freeze), and the Lambda handler
   force-flushes in a `finally` block at the end of every
   invocation. Off Lambda, the standard `BatchSpanProcessor` is
   used. Processor selection is a pure function of its inputs and
   is unit-tested.
9. **Deliberate exceptions to repo rules** (recorded, not silent):
   `platform/telemetry.py` reads `OTEL_*` environment variables
   directly — the second sanctioned exception to "only `settings.py`
   reads env", mirroring the ADR 0005 proxy strip (the OTel SDK
   itself owns that namespace; setup only decides whether an
   endpoint is configured and forces the redaction token). The
   capability spans are the exception to pure tool functions noted
   in decision 3. Both are confined to this one module / two tools.
10. **Out of scope, recorded as follow-ups:** LangGraph-native OTel
    detail (service spans cover LangGraph turns at turn
    granularity); OTel metrics and derived cost (tokens are
    recorded, pricing is never invented); tail sampling; the
    structured audit log (a separate TODO item).

## Consequences

- Every turn is traceable end to end: `agent.turn` → Strands
  `invoke_agent`/`chat`/`execute_tool` → `capability.resolve` /
  `capability.execute` → HTTPX client spans, with the principal and
  the resolved model key on the turn span. LangGraph turns get the
  same `agent.turn` + capability shape, minus framework-native
  detail.
- The `Turn` domain type is unchanged: no usage/stop-reason field
  was added (turns are serialized in all three repositories; the
  framework spans already carry the real values).
- Setup must run before the first `Agent` is constructed (Strands
  reads the redaction env var and binds the provider at first
  tracer creation). The composition root guarantees this:
  `setup_telemetry` runs in `build_app`, and gateways build lazily
  on first request.
- New dependencies, pinned in `pyproject.toml` (no lockfile in this
  repo): `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`,
  `opentelemetry-instrumentation-fastapi`,
  `opentelemetry-instrumentation-httpx` — only what the
  implementation imports. The `opentelemetry-exporter-otlp`
  meta-package is intentionally NOT a dependency (the gRPC exporter
  is not used).
- Tests: 48 new tests (477 backend green, 4 skipped). Setup rules,
  span attributes, content absence, principal/model attribution,
  and the Lambda processor rule are unit-tested against an
  in-memory exporter; Strands' span names, usage attributes, and
  both redaction behaviors are pinned empirically with a stub
  model; one smoke test drives the real OTLP exporter against a
  local stub receiver (arrival + path + content-type only — protobuf
  is never decoded). Tests that install a global provider reset
  OTel/Strands global state in teardown (`otel_global_state`
  fixture) so the suite stays hermetic.
- Verified against the installed Strands 1.57.1 only. A Strands
  upgrade that changes span names, the redaction mechanism, or the
  tracer singleton will trip `tests/test_telemetry_strands.py`
  first — that is its purpose.

## Open items (need a real backend / Rahul)

- Pick the backend (Langfuse Cloud vs self-hosted Phoenix) and wire
  `OTEL_EXPORTER_OTLP_ENDPOINT` + `OTEL_EXPORTER_OTLP_HEADERS`
  (auth) in the deployment — nothing in this change chooses or
  configures one. Then verify live: spans arriving, content
  redaction holding end to end, and the Lambda flush under a real
  freeze/thaw cycle.
- Re-check the Strands content default on every Strands upgrade
  (the empirical tests pin 1.57.1's behavior).
- LangGraph-native span detail and OTel metrics remain follow-ups
  (see TODO.md).
