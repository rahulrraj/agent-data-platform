# ADR 0007: Dynamic per-request model selection

Date: 2026-11-02

## Contents

- [Context](#context)
- [Decision](#decision)
- [Consequences](#consequences)

## Context

The API wired exactly one chat model at startup (the framework ×
provider matrix in the composition root, from `AGENT_MODEL_PROVIDER` /
`AGENT_FRAMEWORK` plus the per-provider model variable). Callers could
not choose a different model per request — e.g. a cheap model for a
simple question and a stronger one for a hard one — without redeploying
with different environment variables. The API has no per-user auth, so
accepting a caller-supplied provider/model pair verbatim would let any
caller run any model at the operator's expense.

## Decision

- Per-request selection is an opaque **key**, sent as an optional
  `model` field on `/api/ask` and `/api/chat`. Absent (or `"default"`)
  means the configured provider + model — today's behavior, unchanged.
- The server owns the mapping: `ALLOWED_MODELS` (parsed and validated in
  `settings.py` only) maps keys to `{provider, model}`. When unset, the
  allowlist is just the configured model. The reserved key `"default"`
  must not appear in `ALLOWED_MODELS` (startup error). Unknown keys are
  a 400 naming the available keys (keys are not secrets).
- The **framework stays as configured** (`AGENT_FRAMEWORK`): a key whose
  provider is incompatible with the framework (langgraph × bedrock) is
  rejected at startup, when the allowlist is built — never at request
  time. Missing credentials for a listed provider surface per request
  as a 502 through the existing `ModelGatewayError` path; startup never
  fails for credentials.
- **Embeddings are not dynamic**: the embedding gateway stays on the
  configured provider path (discovery threshold included). Only the
  answering/chat model switches per request.
- The composition root owns a `ModelGatewayFactory` that lazily builds
  and caches one gateway per key (mirroring the previous
  single-shared-gateway behavior — gateways build a fresh agent per
  turn internally). The service resolves the gateway per call through
  a `ModelGatewayResolver` port contract; it never sees the matrix.
- `GET /api/health` additionally reports the available keys and which
  is default (`models`, `default_model`); the existing `model` label
  field is unchanged.

## Consequences

- Adding a model is a config change (`ALLOWED_MODELS`), not a code
  change — but every new key is a cost/abuse decision by the operator,
  since any caller who can reach the API can use any listed key.
- A key that names a provider without credentials is a latent 502 for
  that key only; the default path and other keys keep working.
- Gateway objects are shared per key across requests (as the single
  gateway was shared before); gateway implementations must stay
  stateless per call, which the existing adapters already are.
- The framework cannot vary per request: a caller cannot ask for a
  LangGraph turn on a Strands-configured deployment, or vice versa.
