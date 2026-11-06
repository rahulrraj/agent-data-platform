# TODO

Living list of remaining work for agent-data-platform. New TODOs go here,
not in scattered docs. Remove an item when it is done — don't just check it
off. Items marked **blocked** need something outside this repo before they
can move.

## Contents

- [Done recently](#done-recently-kept-here-until-the-next-push-so-nothing-gets-lost)
- [Live tests and verification](#live-tests-and-verification)
- [Deployment and infrastructure](#deployment-and-infrastructure)
- [Capabilities and guardrails](#capabilities-and-guardrails)
- [Observability](#observability-implemented-2026-11-03-adr-0009-langgraph-detail-2026-11-04-adr-0010)
- [Agent interoperability](#agent-interoperability)
- [Platform hardening](#platform-hardening)

## Done recently (kept here until the next push, so nothing gets lost)

- Terraform + GitHub Actions pipeline (commit `0ad2e20`): modular Terraform
  (shared modules + one stack per platform: `aws-lambda` and `aws-eks` now,
  `gcp` and other clouds later) and per-platform deploy workflows. EKS
  reuses the existing `deploy/k8s/` manifests. Validated with
  `terraform validate` for both stacks; not yet applied to real AWS.

- Recorded eval set (2026-11-05): `evals/` (cases + fixture registry
  + spec) + `python -m agent_api.evals` runner reusing the test-run
  harness; 8 cases + 4 controls, green. Caught a real bug on its
  first run (`example_value` returned an object for array schemas —
  fixed in `schemas.py`). See `doc/evals.md`.

- OTel metrics (2026-11-06): turn/capability counts + durations and
  a token-usage counter (ADR 0009 decision 11); 14 new tests,
  backend suite 540 passed / 4 skipped, eval set 12/12. Local-only;
  see the push entry below.

## Live tests and verification

- **Bedrock live 3-hop test** — blocked: needs Rahul's AWS credentials in
  the test environment + Bedrock model access enabled for
  `us.anthropic.claude-sonnet-5-5` and `amazon.titan-embed-text-v2:0` in
  `us-east-1`. Then run the 3-hop harness with
  `AGENT_MODEL_PROVIDER=bedrock`.
- **Gemini full 3-hop completion** — unproven. Blocked by Google 503
  capacity errors on the free tier (~7h on 2026-10-24); chaining itself is
  answered (clean 2-hop pass + 1.5-hop partial with smart hop-skipping).
  Options, no decision yet: re-run off-peak, try `gemini-3-flash-preview`
  (Rahul's call — preview build), turn on billing (paid key, reserved
  throughput), or run tests on his own machine.
- **Kubernetes manifests applied to a real cluster** — `deploy/k8s/` is
  written but never applied or tested against a real cluster.
- **Titan discovery-cutoff calibration** — the per-model cutoff for
  `amazon.titan-embed-text-v2:0` is 0.30 PROVISIONAL: a single observed
  true question scored exactly 0.30, with no margin and no false-positive
  calibration. Before trusting 0.30, run the same 15-prompt protocol as
  the Gemini calibration (unrelated / near-miss / true paraphrases over
  the fixture registry) against live Titan embeddings and set the cutoff
  from the measured unrelated ceiling / true floor. The 0.60 value for
  all other models is the calibrated Gemini value; the Ollama default is
  inherited, not calibrated.
- **Bedrock history-replay live check** — the replayed-history shape
  (list-form tool results, tool call → result → answer order) is
  unit-tested only. Needs one live Bedrock turn after a tool call (the
  reported 502 scenario) with AWS credentials to confirm end to end.
- **`nomic-embed-text` install for live Ollama discovery** — Ollama
  capability discovery is test-only today (deterministic fake embeddings);
  the real embedding model is not installed in Rahul's Ollama.
- **Live Okta verification (auth, ADR 0008)** — the implementation is
  unit-tested only (locally generated keys, stubbed JWKS). Still
  unverified against a real Okta tenant: live login redirect +
  callback, real discovery document and JWKS-backed tokens through the
  composed app, refresh-token issuance/rotation and silent renewal,
  the registered redirect URI working end to end, and the Function
  URL `NONE` + CloudFront + WAF front-door interplay. Needs Rahul's
  Okta tenant (issuer, client id, redirect URI registered) and a run
  of the UI + API against it. Cannot be done from this VM.

## Deployment and infrastructure

- **First real AWS deployment** — blocked: needs an AWS account + GitHub
  OIDC identity provider and deploy role (see `deploy/terraform/README.md`
  for the trust-policy snippet), repo variables/secrets
  (`AWS_ROLE_TO_ASSUME`, `AWS_REGION`, `TF_VAR_db_password`,
  `TF_VAR_gemini_api_key`), then merge to `main` or manual dispatch. The
  deploy workflows fail at the OIDC step until then — expected. EKS
  additionally needs an NGINX ingress controller installed before the
  Ingress routes traffic.
- **EKS hardening follow-ups** — IRSA for Bedrock access from EKS pods;
  tighten EKS RDS ingress from VPC-CIDR to the node security group;
  restrict the EKS public API endpoint.
- **True incremental chat streaming on Lambda** — Mangum buffers the whole
  response, so `/api/chat` on Lambda returns one complete SSE document,
  not incremental tokens. True streaming needs a Function URL in
  `RESPONSE_STREAM` mode plus a streaming runtime path. Not faked, not
  built.
- **Shared embedding cache** — the SQLite embedding cache under `/tmp`
  rebuilds on every Lambda cold start (at thousands of capabilities, that
  is thousands of embedding calls before the first discovery). Needs a
  shared or baked-in cache.
- **Deployment strategies: rolling, blue-green, canary** — decide per
  platform. Rolling is the k8s default (Lambda versions roll automatically
  to new invocations); blue-green/canary on Lambda map to aliases with
  weighted routing; EKS canary needs an ingress/mesh mechanism (e.g. Argo
  Rollouts/Flagger). Also noted in `deploy/terraform/README.md`.
- **Push and merge** — remote `feature/initial-platform` tip is
  `921b645` (pushed 2026-11-05 via the connector API script
  `~/workspace/gh_api_push.py` since git HTTPS cannot use the vault
  credential; suites verified green before the push: backend 526
  passed / 4 skipped, eval set 12/12; UI untouched by this batch).
  Local-only since: `9717596` (OTel metrics). The earlier
  Workflows-permission block is resolved (PAT updated 2026-10-25;
  PAT expires Nov 15, 2026 — extend before it lapses or pushing
  breaks). Merging the branch is Rahul's later call.

## Capabilities and guardrails

- **Per-user authorization (`allowed_roles`)** — planned, not built. v1
  acts with the service's permissions by explicit agreement; no per-user
  scoping yet.
- **Authentication (done 2026-11-03, ADR 0008 accepted + implemented;
  live verification against a real Okta tenant still pending)** — UI
  forces login (OIDC, code + PKCE; Okta first, generic OIDC path),
  token on every call, session→principal binding (403 on mismatch),
  per-capability opt-in token forwarding (`forward_user_token`).
  Still unverified: live login, real JWKS, real token forwarding.
  `allowed_roles` still planned (identity first, authorization later).
- **Structured audit log of capability executions** — planned. Today this
  means application logs only.
- **Output filtering, token budgets, input size cap, injection handling**
  — planned gaps from `doc/guardrails.md`.
- **Snowflake-table capability import (`--from snowflake`)** — planned,
  not built. Swagger/OpenAPI import exists.
- **Capability test-run limits** — the swagger question runner
  (built 2026-11-04, see `doc/data-capabilities.md`) uses a
  keyword-embedding stand-in and file-supplied params: it pins
  ranking mechanics and execution, not real embedding quality or
  value extraction. Follow-up: a live-embedding spot check for
  capabilities that warrant it; SQL kinds are not stubbed. The
  recorded eval set (2026-11-05) pins top-level list trimming;
  envelope (`list_paths`) trimming is covered by executor unit
  tests only.
- **Response-trimming guardrail change (done 2026-11-02)** — HTTP
  capabilities can now opt in per capability to `max_results` up to 500
  with `list_paths` / `keep: last` (see `doc/data-capabilities.md`).
  Default stays 50 / keep first; the SQL 50-row cap is unchanged. Note
  for future guardrail reviews: the 500 ceiling is a per-capability
  opt-in, not a new default, and registry values are rejected at load.

## Observability (implemented 2026-11-03, ADR 0009; LangGraph detail 2026-11-04, ADR 0010)

- **OTLP tracing implemented** — Strands-native + OpenInference
  LangGraph telemetry via the global OTel provider +
  framework-agnostic `agent.turn` spans + `capability.resolve`
  / `capability.execute` spans + FastAPI/HTTPX instrumentation; content
  capture forced off (Strands' default emits content unredacted — verified
  empirically, see ADR 0009; the LangGraph instrumentor hides every
  content family via an explicit TraceConfig — verified empirically,
  see ADR 0010); endpoint from `OTEL_EXPORTER_OTLP_*` env,
  silent no-op when unconfigured; 100% sampling default; Lambda uses a
  synchronous span processor + per-invocation force-flush. 71 tests
  (in-memory exporter + stub-model Strands and LangGraph checks +
  stub-receiver smoke test + in-memory metric reader checks). Remaining follow-ups:
  - **Backend stays open by design** (Rahul 2026-11-04): no backend is
    chosen — any OTLP/HTTP backend works via `OTEL_EXPORTER_OTLP_ENDPOINT`
    + headers, with no backend-specific code in the repo (verified by
    audit 2026-11-04). Wiring a backend is deployment config, and live
    verification (spans arriving, redaction holding, Lambda flush under
    freeze/thaw) happens against whichever backend is deployed.
    CloudWatch's OTLP endpoints need SigV4 signing, which the plain
    exporter cannot do — that is a deployment-layer adapter (ADOT),
    not a code change.
- **OTel metrics (done 2026-11-06, ADR 0009 decision 11)** —
    `agent.turn` + `capability.resolve` / `capability.execute`
    counts and durations, plus an `agent.tokens` counter fed by a
    span processor reading the real usage attributes off Strands
    "chat" spans and OpenInference LLM spans (never the
    run-aggregated `invoke_agent` span — that would double-count).
    Export via the standard `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`
    / `OTEL_EXPORTER_OTLP_ENDPOINT`; traces and metrics are
    independent. Derived cost is not computed: pricing is never
    invented. Remaining follow-ups: live verification of metrics
    export against a deployed backend; re-check the Strands usage
    attribute keys on every Strands upgrade (the processor depends
    on `gen_ai.usage.*`, pinned by `tests/test_telemetry_metrics.py`).
  - Re-check the Strands content default on every Strands upgrade
    (`tests/test_telemetry_strands.py` pins 1.57.1's behavior).
  - Re-check the OpenInference content hiding on every OpenInference
    upgrade (`tests/test_telemetry_langgraph.py` pins the 0.1.81 /
    0.1.75 behavior).
- **Structured audit log** (separate item, still open) — see Capabilities
  and guardrails below.

## Agent interoperability

- **A2A (Agent2Agent) support** — let this platform's agent interoperate
  with other agents via Google's A2A protocol. Scope to be decided:
  server side (expose an agent card, accept A2A tasks/messages), client
  side (discover and delegate to remote A2A agents), or both.

## Platform hardening

- **Circuit breaker, API rate limiting, failover testing** — planned,
  not built.
- **LangGraph × Bedrock gateway** — intentionally not built (raises a
  clear error today). Build it only if a LangGraph + Bedrock combination
  is actually needed.
