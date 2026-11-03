# Platform Principles

Cross-cutting qualities for this agent platform: **cloud-agnostic**,
**resilient**, **multi-runtime**, **model-agnostic**.

Companion to `doc/hexagonal-architecture.md` (which governs *structure* —
where code lives and what may depend on what). This doc governs *platform
qualities* — what the system must hold true regardless of where it runs or
which model answers.

## Contents

- [1. Cloud agnostic](#1-cloud-agnostic)
- [2. Resilient](#2-resilient)
- [3. Multiple runtimes](#3-multiple-runtimes)
- [4. Model agnostic](#4-model-agnostic)
- [Conformance checklist](#conformance-checklist)

## 1. Cloud agnostic

The platform runs on any cloud, or none. No cloud-provider SDKs, managed
services, or proprietary APIs in the core.

- **Container-first.** The `Dockerfile` is the deployment contract. Nothing in
  the codebase assumes AWS, GCP, or Azure.
- **12-factor config.** All settings come from environment variables, loaded
  once in `config/settings.py`. No baked-in endpoints, regions, or credentials.
- **State behind ports.** Anything that could become a managed service
  (conversation history today, vector memory tomorrow) sits behind a port.
  `ConversationRepository` has in-memory, SQLite, and Postgres adapters
  behind the same port; switching stores never touches services.
- **Ruled out:** provider SDK types outside `adapters/`, cloud-specific
  queues/caches wired directly into services, credentials fetched anywhere
  except through config.

## 2. Resilient

Failures are expected — models time out, networks blip. The platform degrades
in predictable ways instead of hanging or leaking internals.

- **Translate at boundaries.** Adapter failures become `ModelGatewayError`,
  services translate to `AgentServiceError`, controllers map to HTTP 502.
  Callers never see raw tracebacks or provider SDK exceptions.
- **Bound every outbound call.** Timeouts on model requests; `max_tokens`
  caps generation length (small models can otherwise ramble until the
  context window ends).
- **Retry transient failures** with exponential backoff and jitter, via
  `platform/retry.py`. Adapters classify their own transient errors; streams
  retry only before the first chunk is yielded.
- **Health with teeth.** `/api/health` is liveness; `/api/ready` is readiness
  (model reachable, store writable) for load balancers.
- **Stateless where it counts.** Gateways are stateless per call, so any
  instance can serve any request. History durability comes from the store
  adapter: in-memory (default, process-local), SQLite (`CONVERSATION_STORE=sqlite`,
  survives restarts, single host), or Postgres (`CONVERSATION_STORE=postgres`,
  shared across instances for multi-instance deployments).

## 3. Multiple runtimes

One core, many entry points (architecture reference §8). The HTTP API is one
controller; a CLI, a chat widget backend, or a scheduled job are others —
all sharing `services/`, `domain/`, `ports/`, and `tools/` unchanged.

- **Rule:** a new runtime = a new controller + wiring in the composition
  root (`composition.py`; `main.py` is a thin CLI over
  `composition.build_app()`). Never duplicate the core, never bypass the
  service layer.
- Controllers stay thin: parse input, call the use case, map the result.
  All orchestration lives in services.

## 4. Model agnostic

The platform must not care which model answers — local, free-tier, or paid.

- **All model access goes through the `ModelGateway` port.** Provider SDKs
  appear only in `adapters/`. Services and controllers never import them.
- **Tools are provider-neutral.** Tool functions are plain Python +
  `@tool` metadata; each adapter translates them into its runtime's tool
  format. No provider types in tool code.
- **Provider selection lives in exactly one place:**
  `composition.build_gateway()` (and `composition.build_tool_deps()` for
  embeddings), driven by config. Adding a provider = new adapter
  + one branch in the composition root.
- **Prove it per provider.** Every model adapter gets an integration test
  against the real provider (see `tests/adapters/`). A provider without a
  passing integration test is not supported.

## Conformance checklist

| Principle        | Status | Notes                                                                 |
| ---------------- | ------ | --------------------------------------------------------------------- |
| Cloud agnostic   | ✅     | No cloud SDKs; Dockerfile; env-only config; state behind ports        |
| Resilient        | ✅     | Error translation ✅; bounded calls ✅; retries with backoff ✅        |
|                  |        | (`platform/`); readiness probe ✅; SQLite store ✅ (durable, 1 host)   |
|                  |        | Postgres store ✅ (shared across instances, multi-instance ready)     |
| Multi-runtime    | ✅     | Controller seam exists; HTTP built, core untouched by design          |
| Model agnostic   | ✅     | Port + provider adapters × framework switch (strands\|langgraph, ADR 0003) |

**Legend:** ✅ holds today · ❌ planned
