# Guardrails

What constrains the agent today, and what is still planned. Status is
honest: ✅ enforced in code · 🟡 partial/documented · ❌ planned, not built.

## 1. Input guardrails

| Guardrail | Status | Notes |
| --- | --- | --- |
| Empty / missing message rejected | ✅ | `InvalidMessageError` → HTTP 400 |
| Message size cap | ❌ | No max length enforced yet |
| Prompt-injection handling on tool/RAG output | ❌ | Tool results are fed back unfiltered |

## 2. Tool and data guardrails

| Guardrail | Status | Notes |
| --- | --- | --- |
| Tools are version-controlled code | ✅ | New tools need approval (`doc/approvals.md`) |
| Data only via capability registry | ✅ | Model supplies parameter *values*; queries/URLs are fixed in YAML |
| `mode: read` enforced | ✅ | Executors refuse anything else |
| Parameter validation | ✅ | Required, types, unknown params rejected — clean errors, never tracebacks; safe string values coerced (`"123"` → `123`, `"true"` → `true`) |
| 50-row cap + `truncated` flag | ✅ | Per execution, for SQL rows and HTTP list results |
| Minimal columns | ✅ | Registry declares only needed fields |
| Secrets out of the repo | ✅ | Connections live in `DATA_CONNECTIONS` (env JSON), never in YAML |
| Per-user authorization | 🟡 | Documented: the agent acts with the service's permissions; per-user scoping (`allowed_roles`) is planned |
| Audit log of capability executions | ❌ | Planned — today this means application logs |

## 3. Model guardrails

| Guardrail | Status | Notes |
| --- | --- | --- |
| Provider allowlist | ✅ | `ollama` / `gemini` / `bedrock`, validated in settings |
| Framework allowlist | ✅ | `strands` / `langgraph` only |
| Output filtering (PII, secrets) | ❌ | Nothing filters model output today |
| Token / cost budgets | ❌ | Matters once Gemini (metered) is the provider |

## 4. Operational guardrails

| Guardrail | Status | Notes |
| --- | --- | --- |
| Retry with backoff on transient failures | ✅ | `platform/retry.py`; streams retry only before the first chunk |
| Fail fast on bad input | ✅ | No retries for validation errors |
| Liveness + readiness probes | ✅ | `/api/health`, `/api/ready` |
| Stateless gateways | ✅ | History durability comes from the store adapter |
| Dev-server watchdog | 🟡 | Hook restarts dead dev servers; not a production story |
| Frozen-rule discipline | ✅ | No tuning on the test set; no mid-test rule changes |

## 5. Development guardrails

| Guardrail | Status | Notes |
| --- | --- | --- |
| Tests per layer (arch doc §9) | ✅ | 216 backend tests green, 4 skipped (2026-10-25; update this row in the same commit as any suite change) |
| ADRs for architectural decisions | ✅ | `doc/adr/` |
| Approvals for gated changes | ✅ | `doc/approvals.md` |
| No invented scope/content | ✅ | Build what was asked; proposals before commits |

## Reading this table

A ❌ is not a failure — it is a named gap with an owner (whoever picks it
up). Promote a row to ✅ only when the enforcement exists in code or in a
running process, not when it is merely agreed.
