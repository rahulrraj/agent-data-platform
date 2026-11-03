# Documentation index

Every document in this repo, in one place. Each document below also
carries its own `Contents` section at the top.

## Contents

- [Start here](#start-here)
- [Architecture and principles](#architecture-and-principles)
- [Data capabilities](#data-capabilities)
- [Lifecycle, guardrails, approvals](#lifecycle-guardrails-approvals)
- [Decision records](#decision-records)
- [Frontend and infrastructure](#frontend-and-infrastructure)
- [Review record](#review-record)

## Start here

| Document | What it is |
| --- | --- |
| [`../README.md`](../README.md) | Platform overview: how a question is answered, layers, switching framework/model, resilience, observability, run/deploy, API, project map |
| [`../AGENTS.md`](../AGENTS.md) | Mandatory working rules for this codebase + pointers to detail |
| [`../TODO.md`](../TODO.md) | Living list: follow-ups, planned gaps, blocked verifications — review before implementing anything |
| [`developer-guide.md`](developer-guide.md) | Working notes: layout, tool/provider conventions, operations, sandbox quirks |

## Architecture and principles

| Document | What it is |
| --- | --- |
| [`hexagonal-architecture.md`](hexagonal-architecture.md) | Structure rules copied from the original Drive doc: layers, ports, dependency directions, placement test |
| [`platform-principles.md`](platform-principles.md) | Platform qualities: cloud-agnostic, resilient, multi-runtime, model-agnostic + conformance checklist |
| [`architecture-reference.md`](architecture-reference.md) | Checklist distilled from building this platform — the boundaries to get right first time on the next project |

## Data capabilities

| Document | What it is |
| --- | --- |
| [`data-capabilities.md`](data-capabilities.md) | Registry format, discovery/execution flow, embedding cache, guardrails, access model |
| [`../skills/capability-generator/SKILL.md`](../skills/capability-generator/SKILL.md) | Project skill: draft capability YAML from a Swagger/OpenAPI spec, review, merge, test |

## Lifecycle, guardrails, approvals

| Document | What it is |
| --- | --- |
| [`agent-lifecycle.md`](agent-lifecycle.md) | ADLC: stages, gates, and the ship checklist |
| [`guardrails.md`](guardrails.md) | Guardrail catalog — enforced vs. planned, honestly marked |
| [`approvals.md`](approvals.md) | Approval matrix, what counts as approval, emergency path |

## Decision records

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

## Frontend and infrastructure

| Document | What it is |
| --- | --- |
| [`../src/ui/README.md`](../src/ui/README.md) | React chat UI: run it, auth, deployment wiring, history model, tests, layout |
| [`../deploy/terraform/README.md`](../deploy/terraform/README.md) | Terraform stacks (Lambda + EKS), state backend, pipelines/OIDC, first deployment, known follow-ups |

## Review record

| Document | What it is |
| --- | --- |
| [`code-review-2026-10-24.md`](code-review-2026-10-24.md) | Multi-agent code review (8 Critical, 31 Major, 48 Minor). Frozen record of the review as delivered — a `Contents` section was added for navigation; the findings themselves are unedited. Intentionally uncommitted. |
