# Documentation index

Read the book straight through, in order — or jump to a chapter from
here. Every document below also carries its own `Contents` section at
the top. The root [`../README.md`](../README.md) is Part 1; this index
is the map for the rest.

## Contents

- [Part 1 — What the system is](#part-1--what-the-system-is)
- [Part 2 — Orientation / architecture](#part-2--orientation--architecture)
- [Part 3 — Running it locally](#part-3--running-it-locally)
- [Part 4 — Building on it](#part-4--building-on-it)
- [Part 5 — Deploying it](#part-5--deploying-it)
- [Part 6 — Reference](#part-6--reference)

## Part 1 — What the system is

| Document | What it is |
| --- | --- |
| [`../README.md`](../README.md) | Platform overview: system and sequence diagrams, how a question is answered (including the relevance-selected history window and per-user sessions), capability discovery, layers, switching framework/model, resilience, observability, running, deploying, API, project map |
| [`../AGENTS.md`](../AGENTS.md) | Mandatory working rules for this codebase + pointers to detail |
| [`../TODO.md`](../TODO.md) | Living list: follow-ups, planned gaps, blocked verifications — review before implementing anything |

## Part 2 — Orientation / architecture

| Document | What it is |
| --- | --- |
| [`hexagonal-architecture.md`](hexagonal-architecture.md) | Structure rules copied from the original Drive doc: layers, ports, dependency directions, placement test |
| [`platform-principles.md`](platform-principles.md) | Platform qualities: cloud-agnostic, resilient, multi-runtime, model-agnostic + conformance checklist |
| [`architecture-reference.md`](architecture-reference.md) | Checklist distilled from building this platform — the boundaries to get right first time on the next project |

## Part 3 — Running it locally

| Document | What it is |
| --- | --- |
| [`run-locally.md`](run-locally.md) | Run it locally, three ways: no containers, containers, Kubernetes (kind) |
| [`../deploy/local/README.md`](../deploy/local/README.md) | Local three-container stack (API + UI + nginx front door), Okta login end to end |
| [`../src/ui/README.md`](../src/ui/README.md) | React chat UI: run it, auth, deployment wiring, history model, tests, layout |

## Part 4 — Building on it

| Document | What it is |
| --- | --- |
| [`data-capabilities.md`](data-capabilities.md) | Registry format, discovery/execution flow, embedding cache, guardrails, access model |
| [`../skills/capability-generator/SKILL.md`](../skills/capability-generator/SKILL.md) | Project skill: draft capability YAML from a Swagger/OpenAPI spec, review, merge, test |
| [`evals.md`](evals.md) | Recorded eval set: golden capability-behavior cases, run offline with `python -m agent_api.evals` |
| [`developer-guide.md`](developer-guide.md) | Contributor handbook: layout, tool/provider conventions, operations, sandbox quirks |

## Part 5 — Deploying it

| Document | What it is |
| --- | --- |
| [`../deploy/k8s/README.md`](../deploy/k8s/README.md) | Kubernetes manifests (any cloud): selective apply order, in-cluster Postgres vs. managed database, TLS warning |
| [`../deploy/terraform/README.md`](../deploy/terraform/README.md) | Terraform stacks (Lambda + app-only EKS), state backend, pipelines/OIDC, GitLab CI, first deployment, EKS state-migration hazard, known follow-ups |
| [`../.gitlab-ci.yml`](../.gitlab-ci.yml) | GitLab CI mirror of the GitHub pipeline (tests, ECR build+push, disabled deploy placeholder) — setup notes in its header comments and in the Terraform README's GitLab CI section |

## Part 6 — Reference

Process reference:

| Document | What it is |
| --- | --- |
| [`agent-lifecycle.md`](agent-lifecycle.md) | ADLC: stages, gates, and the ship checklist |
| [`guardrails.md`](guardrails.md) | Guardrail catalog — enforced vs. planned, honestly marked |
| [`approvals.md`](approvals.md) | Approval matrix, what counts as approval, emergency path |

Decision records (appendix — do not renumber):

| Document | Decision |
| --- | --- |
| [`adr/0001-tools-top-level-folder.md`](adr/0001-tools-top-level-folder.md) | Tools live in a top-level `tools/` folder, not under `domain/` |
| [`adr/0002-data-capability-registry.md`](adr/0002-data-capability-registry.md) | Data access via a declared capability registry, not per-entity tools |
| [`adr/0003-dual-agent-frameworks.md`](adr/0003-dual-agent-frameworks.md) | Strands × LangGraph frameworks, resolved in the composition root |
| [`adr/0004-no-composite-capabilities.md`](adr/0004-no-composite-capabilities.md) | Composite capabilities rejected — the model's working set stays concise |
| [`adr/0005-proxy-handling-in-composition-root.md`](adr/0005-proxy-handling-in-composition-root.md) | Proxy env vars are stripped in the composition root only |
| [`adr/0006-shared-executor-base.md`](adr/0006-shared-executor-base.md) | Shared bases for gateways, subclassed per port |
| [`adr/0007-dynamic-model-selection.md`](adr/0007-dynamic-model-selection.md) | Per-request model selection via a server-side `ALLOWED_MODELS` allowlist |
| [`adr/0008-authentication.md`](adr/0008-authentication.md) | OIDC login (Okta first, extendable), bearer tokens, per-capability token forwarding |
| [`adr/0009-observability-otlp.md`](adr/0009-observability-otlp.md) | OTLP tracing: Strands-native + service spans, content redaction forced on |
| [`adr/0010-langgraph-otel-detail.md`](adr/0010-langgraph-otel-detail.md) | LangGraph detail via OpenInference, every content family hidden by an explicit TraceConfig |

Review record (frozen — findings are unedited):

| Document | What it is |
| --- | --- |
| [`code-review-2026-10-24.md`](code-review-2026-10-24.md) | Multi-agent code review (8 Critical, 31 Major, 48 Minor). Frozen record of the review as delivered — a `Contents` section was added for navigation; the findings themselves are unedited. Intentionally uncommitted. |
| [`code-review-2026-11-06.md`](code-review-2026-11-06.md) | Multi-agent code review, second round (six reviewers, suite re-run 540 passed / 4 skipped). Frozen record as delivered; findings unedited. Intentionally uncommitted. |
