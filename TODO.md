# TODO

Living list of remaining work for agent-data-platform. New TODOs go here,
not in scattered docs. Remove an item when it is done — don't just check it
off. Items marked **blocked** need something outside this repo before they
can move.

## Done recently (kept here until the next push, so nothing gets lost)

- Terraform + GitHub Actions pipeline (commit `0ad2e20`): modular Terraform
  (shared modules + one stack per platform: `aws-lambda` and `aws-eks` now,
  `gcp` and other clouds later) and per-platform deploy workflows. EKS
  reuses the existing `deploy/k8s/` manifests. Validated with
  `terraform validate` for both stacks; not yet applied to real AWS.

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
- **Push and merge** — remote `feature/initial-platform` is fully pushed
  (all commits including this TODO update; push via the connector API
  script `~/workspace/gh_api_push.py` since git HTTPS cannot use the vault
  credential). The earlier Workflows-permission block is resolved (PAT
  updated 2026-10-25). Merging the branch is Rahul's later call.

## Capabilities and guardrails

- **Per-user authorization (`allowed_roles`)** — planned, not built. v1
  acts with the service's permissions by explicit agreement; no per-user
  scoping yet.
- **Authentication (ADR 0008, proposed 2026-11-03, awaiting Rahul's
  approval — nothing implemented)** — UI forces login (OIDC, code +
  PKCE), token on every call, session→principal binding (403 on
  mismatch), per-capability opt-in token forwarding to downstream HTTP
  APIs (`forward_user_token`). Provider decided 2026-11-03: Okta first,
  extendable (generic OIDC path; Okta specifics in config/adapter
  only). Four open decisions left in the ADR (api_key-vs-forwarding
  rule, fail-closed startup, machine-client flow, UI token storage).
  This unblocks `allowed_roles` and the C-1 × C-5 browser/UI tension;
  identity first, authorization later.
- **Structured audit log of capability executions** — planned. Today this
  means application logs only.
- **Output filtering, token budgets, input size cap, injection handling**
  — planned gaps from `doc/guardrails.md`.
- **Snowflake-table capability import (`--from snowflake`)** — planned,
  not built. Swagger/OpenAPI import exists.
- **Recorded eval set for behavior changes** — planned (see
  `doc/agent-lifecycle.md`).
- **Response-trimming guardrail change (done 2026-11-02)** — HTTP
  capabilities can now opt in per capability to `max_results` up to 500
  with `list_paths` / `keep: last` (see `doc/data-capabilities.md`).
  Default stays 50 / keep first; the SQL 50-row cap is unchanged. Note
  for future guardrail reviews: the 500 ceiling is a per-capability
  opt-in, not a new default, and registry values are rejected at load.

## Observability (to review)

- **Review how observability is handled** — current state is thin: application
  logs only (no structured audit log of capability executions, no tracing,
  no per-turn token/latency/cost recording, no task-level records). Review
  what we should record per turn and per tool/capability call, where it
  should live, and what it costs — using Conductor OSS's automatic
  per-task records (prompt, response, tokens, latency, retries) as one
  reference point. Related: the structured audit log item under
  Capabilities and guardrails below.
- Review done 2026-10-25 (OTLP research + page `agent-observability-otlp-review`):
  Strands is natively OTLP; recommended minimal setup is framework-native
  OTel + FastAPI/HTTPX instrumentation → direct OTLP on Lambda (with flush,
  else the freeze/thaw trap loses spans) / Collector on EKS → Langfuse
  Cloud or self-hosted Phoenix; per-turn and per-capability record specs,
  sampling, PII handling, and retention are on the page. Remaining: pick a
  backend and wire the exporter in (not started).

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
