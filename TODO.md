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

- Local container stack (2026-11-25): `deploy/local/` — `run.sh`
  (build/up/down) + `front-door.conf` + README, reconstructed from
  Rahul's working 2026-11-24 run on his laptop (his values replaced by
  env vars; nothing secret in the repo). Base images from ECR Public
  (Docker Hub 429s on this VM); front door logs no query strings (the
  Okta code never hits the log) and disables buffering with a 900s
  read timeout for `/api/chat`. README "Run with Docker" section
  added. Closes the "no local container run docs" gap from the
  2026-11-06 review's deployment findings.

- Local run guide, three ways (2026-11-25): `doc/run-locally.md` —
  no containers (venv + Vite dev server), containers
  (`deploy/local/`), Kubernetes (kind + ingress-nginx + self-signed
  TLS). k8s path written from the manifests, NOT yet run end to end
  (no cluster on this VM); its unverified steps are marked in the
  guide. Supporting changes: `deploy/local/kind-config.yaml` (host
  80/443 mappings); `AUTH_ISSUER`/`AUTH_AUDIENCE` placeholders added
  to `deploy/k8s/configmap.yaml` — the manifests previously wired no
  auth at all, so pods would fail closed on startup.

- No-decision review batch, done (2026-11-25, Rahul: "Can you do that
  by yourself"): the items from the 2026-11-25 laptop-doc review that
  needed no decision from him. (1) All three Dockerfiles pull bases
  from ECR Public (`public.ecr.aws/...`); `deploy/local/run.sh` no
  longer pulls-and-retags and passes the UI settings as build args,
  and its front-door container uses the ECR image name too. (2) UI
  Dockerfile takes `VITE_OIDC_ISSUER` / `VITE_OIDC_CLIENT_ID` /
  `VITE_OIDC_AUDIENCE` / `VITE_API_URL` build args (all default
  empty); verified the mechanism by building with test values and
  finding them baked into `dist/assets/*.js`. (3) Swagger shows an
  Authorize button: `HTTPBearer(auto_error=False)` declared on
  `/api/ask` and `/api/chat` only — documentation only, enforcement
  is unchanged (`_authenticate` still gates); verified in
  `/openapi.json`, health/ready stay public. (4) `node_modules`
  untracked (`git rm --cached`, 5,126 files; stays on disk locally)
  and `src/ui/node_modules/` added to `.gitignore`; a fresh checkout
  runs `npm ci` first. Verified: backend 489 passed / 5 skipped
  (19 telemetry failures are a pre-existing venv gap — missing
  `opentelemetry.exporter`/`mangum`, untouched by this change),
  controller tests 34 passed / 1 skipped, UI tests 93 passed.
  NOT verified here: no docker on this VM, so no image was actually
  built — run `deploy/local/run.sh build` on a machine with docker
  before trusting the Dockerfiles. NOT done (needs his call): the
  deploy workflows still build the UI without build args, so a
  deployed UI still gets no Okta settings baked in.

- Pipeline (2026-11-25): `.github/workflows/pipeline.yml` — stage 1
  unit tests (shared `reusable-build-test.yml`: backend pytest + UI
  vitest + Terraform fmt/validate), stage 2 builds the API + UI
  images and pushes them to ECR (registry built from the OIDC
  account ID + region, repos `<project>-<environment>-api` / `-ui`,
  tag = commit SHA; re-runs skip tags already in ECR), stage 3
  deploy is a disabled placeholder (`if: false`) until the deploy
  target and strategy are picked. Runs on push to `main` +
  `feature/initial-platform` and on manual dispatch; no PR trigger
  (ci.yml already tests PRs, and a PR must not push images). Safe to
  merge before AWS is wired in: stage 1 runs without AWS, stage 2
  fails at the OIDC step until the role exists. YAML validated; never
  run on GitHub yet. Pushed 2026-11-25 (commit `55a7c2c`).

- Per-user history (2026-11-25): `ConversationRepository`
  gains `list_sessions(owner)` (port + in-memory/SQLite/Postgres;
  Postgres filters in SQL on the `turns_json` owner envelope +
  expression index, dev mode lists everything); `GET
  /api/sessions` returns only the caller's sessions (401 when
  unauthenticated); the UI stores the id_token `sub`, scopes its
  localStorage copy by principal, and reconciles once per login
  against the server list. Backend 568 passed / 4 skipped, eval
  12/12, UI 109 passed. Known limitation: the reconcile runs once
  right after login — a local session created in the split second
  before the fetch resolves could be dropped (judged negligible).
  Not verified against live Postgres (filter + index only
  faked-psycopg tested) — see the live-test item below.

- Relevance-based history window (2026-11-25): new
  `services/history_selection.py` — ranking by embedding similarity
  (top 2 most relevant past exchanges + the immediately preceding
  one, ≤ 3 exchanges; 40 turns only an absolute ceiling; recency
  fallback when embeddings are unavailable or fail, recorded in the
  result + log). Wired into `ask_agent._gateway_conversation` for
  both `ask()` and `ask_stream()`. 13 selection tests + 3 service
  tests; suite numbers as above.
  **ON HOLD (Rahul, 2026-11-25):** the service now defaults to
  recency — the last 2 exchanges, no embedding call at all
  (`HISTORY_SELECTION_MODE`, default `recency`; `AskAgentService`
  takes `history_selection_mode`, default `"recency"`). The
  relevance code stays behind `mode="relevance"` for the separate
  PR below; docs corrected to recency.

- Separate PR (deferred, Rahul 2026-11-25): relevance history
  selection + stored turn vectors. The current relevance path
  re-embeds every stored exchange on every turn and the vectors
  live nowhere — wasted work. The PR should embed each turn once
  (vector column / pgvector or a cache keyed by turn, invalidated
  on edit), rank against stored vectors, and flip the default to
  `HISTORY_SELECTION_MODE=relevance`. Keep the recency fallback
  for embedding failures.

- EKS stack is app-only (2026-11-25): `stacks/aws-eks` no longer
  provisions the cluster, node groups, addons, VPC/subnets, or
  RDS — it takes an existing cluster (`cluster_name`) and an
  existing Postgres (`postgres_dsn`, sensitive) as inputs and
  creates only ECR repos, the cluster OIDC provider, and the IRSA
  role for the API pods (trust-pinned to
  `system:serviceaccount:agent-platform:agent-api`). New
  `deploy/k8s/serviceaccount.yaml`; workflow reads
  `EKS_CLUSTER_NAME` + `TF_VAR_postgres_dsn`. `terraform
  validate` clean on both stacks. STATE HAZARD: old remote state
  still holds the removed resources — `terraform state rm` them
  before the first apply or it plans destruction of the cluster,
  VPC, and RDS (documented in `deploy/terraform/README.md`).
  Lambda stack is app-only for networking too (2026-11-25):
  `stacks/aws-lambda` takes an existing VPC (`vpc_id`) and private
  subnets (`private_subnet_ids`, ≥2, must have a NAT route for
  model-API egress) as inputs; `modules/networking` deleted
  (it was the last user). Same state hazard for the VPC:
  `terraform state rm module.networking` before the first apply.
  RDS creation stays in the Lambda stack (the AWS-specific piece).
  Docs updated by the book-form reorg pass (item 4, in progress).

- GitLab CI (2026-11-25): `.gitlab-ci.yml` mirrors the GitHub
  pipeline (tests with no AWS; build+push API/UI images to ECR
  via GitLab OIDC `id_tokens` + `assume-role-with-web-identity`;
  deploy placeholder `rules: when: never`). YAML validated;
  NOT run on a real GitLab runner. Setup needed before stage 2
  can run: `AWS_ROLE_TO_ASSUME` variable + OIDC provider for
  `https://gitlab.com` and a trusting role (see the file's header
  comments); the `sub` claim format is the documented default,
  unconfirmed against a real token.

- Docs reorganized into book form (2026-11-25): root README +
  `doc/README.md` index in 6 parts (what it is → architecture →
  running locally → building on it → deploying → reference);
  intro + diagrams kept, ADRs unrenumbered appendix,
  developer-guide stays the contributor handbook. Terraform docs
  now describe the app-only EKS stack (existing cluster +
  Postgres as inputs; creates ECR, OIDC provider, IRSA role)
  including the state-migration hazard and the GitLab CI setup;
  per-user history (`GET /api/sessions`) and the history window
  (recency now; relevance on hold) corrected wherever docs stated
  the old behavior;
  k8s README + run-locally step 5 gain `serviceaccount.yaml`
  before `api-deployment.yaml`; `src/ui` README history section
  corrected. Anchor check over all 295 doc links: 0 broken (one
  pre-existing placeholder link in the frozen 2026-10-24 review
  left alone).

## Live tests and verification

- **Per-user sessions list against live Postgres** — the
  `turns_json->>'owner'` filter and expression index in
  `PostgresConversationRepository.list_sessions` were verified
  only via faked-psycopg tests. Run against real Postgres on the
  first Lambda/staging deploy (list for two principals + dev mode).
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
- **Live Okta verification (auth, ADR 0008)** — partly verified by
  Rahul's local container run (2026-11-24, `deploy/local/`): live login
  redirect + callback, real discovery document and JWKS-backed tokens
  through the composed app, and the registered redirect URI working end
  to end — login + chat worked. Still unverified: refresh-token
  issuance/rotation and silent renewal, and the Function URL `NONE` +
  CloudFront + WAF front-door interplay. Cannot be done from this VM.

## Deployment and infrastructure

- **First real AWS deployment** — blocked: needs an AWS account + GitHub
  OIDC identity provider and deploy role (see `deploy/terraform/README.md`
  for the trust-policy snippet), repo variables/secrets
  (`AWS_ROLE_TO_ASSUME`, `AWS_REGION`, `TF_VAR_db_password`,
  `TF_VAR_gemini_api_key`), then merge to `main` or manual dispatch. The
  deploy workflows fail at the OIDC step until then — expected. EKS
  additionally needs an NGINX ingress controller installed before the
  Ingress routes traffic, plus repo variable `EKS_CLUSTER_NAME` (the
  existing cluster's name) and secret `TF_VAR_postgres_dsn` (DSN of the
  existing Postgres); before the first EKS apply, `terraform state rm`
  the resources the app-only stack no longer manages (cluster, node
  group, addons, networking, postgres, cluster/node IAM roles) or it
  plans their destruction.
- **EKS hardening follow-ups** — IRSA for Bedrock access from EKS pods
  is built (2026-11-25, app-only stack). No longer applicable as
  Terraform items: EKS RDS ingress (the EKS stack provisions no RDS)
  and the EKS public API endpoint (no longer Terraform-managed).
- **k8s secrets via External Secrets Operator + AWS Secrets Manager**
  (deferred 2026-11-25, Rahul: "not for now, maybe for future") —
  syncs into `agent-platform-secrets`; the Deployment already reads
  that name; kind/local keeps the hand-created Secret. Revisit only
  if he re-raises it.
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
- **Pipeline stage 3 (deploy)** — `.github/workflows/pipeline.yml`
  has a disabled placeholder deploy job (`if: false`); wire it once
  the deploy target (Lambda vs EKS) and the deployment strategy are
  picked — either a deploy step in the pipeline or a call to the
  per-platform deploy workflow.
- **Push and merge** — remote `feature/initial-platform` tip is
  `2d5e77f` (pushed 2026-11-25, verified — all 13 commits replayed
  with identical SHAs: run guide namespace fix, TODO notes, per-user
  history + relevance window, app-only EKS, GitLab CI, deferred
  External Secrets Operator, capability description overrides, docs
  book reorg, recency-by-default history, Bedrock Sonnet kind run).
  Note: the push worked with the stored credential, so the Nov 15
  PAT-expiry warning is stale — the credential is valid as of
  2026-11-25. Push on Rahul's word via the connector API script
  `~/workspace/gh_api_push.py` (git HTTPS cannot use the vault
  credential). Merging the branch is Rahul's later call.

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
- **Capability description overrides (done 2026-11-25)** — `--overrides
  overrides.yaml` on the capability generator
  (`src/agent_api/capability_import/overrides.py`): a human-edited,
  generator-read-only file keyed by capability id replaces the Swagger
  capability/parameter descriptions deterministically on every re-run;
  unknown ids/parameters warn, malformed file exits 2. See
  `skills/capability-generator/SKILL.md` ("Override poor spec
  descriptions"). Still open: overrides are keyed by generated id, so
  renaming an `operationId` orphans the override (warned, not applied);
  no override support for `fields`/`list_paths`, or for
  `--from snowflake` (planned, not built).
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
