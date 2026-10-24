# TODO

Living list of remaining work for agent-data-platform. New TODOs go here,
not in scattered docs. Remove an item when it is done — don't just check it
off. Items marked **blocked** need something outside this repo before they
can move.

## In progress (not TODO, but tracked here so nothing gets lost)

- Terraform + GitHub Actions pipeline: modular Terraform (shared modules +
  one stack per platform: `aws-lambda` and `aws-eks` now, `gcp` and other
  clouds later) and per-platform deploy workflows. EKS reuses the existing
  `deploy/k8s/` manifests. Being built on `feature/initial-platform`.

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
- **`nomic-embed-text` install for live Ollama discovery** — Ollama
  capability discovery is test-only today (deterministic fake embeddings);
  the real embedding model is not installed in Rahul's Ollama.

## Deployment and infrastructure

- **First real AWS deployment** — blocked: needs an AWS account + IAM role,
  ECR push, Lambda function + Function URL, and env vars
  (`CONVERSATION_STORE=postgres`, `POSTGRES_DSN`, provider + keys). The
  code for it is the in-progress Terraform/pipeline work above.
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
- **Push and merge** — commits `9324694` (Bedrock), `6a8e612` (Lambda),
  and the infra work are local-only on `feature/initial-platform`.
  Push when Rahul asks; merging the branch is his later call.

## Capabilities and guardrails

- **Per-user authorization (`allowed_roles`)** — planned, not built. v1
  acts with the service's permissions by explicit agreement; no per-user
  scoping yet.
- **Structured audit log of capability executions** — planned. Today this
  means application logs only.
- **Output filtering, token budgets, input size cap, injection handling**
  — planned gaps from `doc/guardrails.md`.
- **Snowflake-table capability import (`--from snowflake`)** — planned,
  not built. Swagger/OpenAPI import exists.
- **Recorded eval set for behavior changes** — planned (see
  `doc/agent-lifecycle.md`).

## Platform hardening

- **Circuit breaker, API rate limiting, failover testing** — planned,
  not built.
- **LangGraph × Bedrock gateway** — intentionally not built (raises a
  clear error today). Build it only if a LangGraph + Bedrock combination
  is actually needed.
