# ADR 0002: Generic data-capability registry for organization data

**Status:** accepted
**Date:** 2026-10-21
**Context:** the agent needs organization data (Snowflake, SQL Server, REST
APIs). Hand-writing one tool per entity (`customer_lookup`, `order_lookup`,
…) does not scale and scatters data-access rules across tools.

**Decision:** a generic, registry-driven data layer:

- `registry/capabilities.yaml` declares each capability: id, description,
  kind (`snowflake` | `sqlserver` | `http`), named connection, parameters,
  and the fixed query/request. The model supplies parameter *values* only.
- Two generic tools: `discover_capability(need)` (semantic search over
  descriptions via an `EmbeddingGateway` port) and
  `execute_capability(id, params)` (validates params, dispatches to the
  executor for the capability's kind).
- Ports: `CapabilityRepository` (the registry — app-owned data),
  `EmbeddingGateway` (other-system capability),
  `CapabilityExecutor` (one adapter per kind — other-system capability).
- Factory plugins: tool folders expose `create_tool(deps)`; `ToolDeps`
  carries registry/embeddings/executors. Bare @tool functions keep working.

**Rationale:**
- Follows the reference's port rules: registry = repository (app-owned),
  executors/embeddings = gateways (other systems). Adapters own all SDKs.
- Adding a data source = a YAML entry (+ named connection in
  `DATA_CONNECTIONS`). No code, no new tool.
- Semantic discovery keeps the model from needing to know capability ids.

**Consequences:**
- The registry is the access control plane: whoever can edit it decides what
  data the agent can reach. It is version-controlled config — edits need repo
  access, review, and deploy. See "Access" in `doc/data-capabilities.md`.
- No per-user authorization in v1: the agent acts with the service's
  permissions. Per-user scoping (`allowed_roles` + request identity) is a
  planned follow-up, not built.
- Executors must stay read-only (`mode: read` enforced) and return minimal
  fields; results land in the model's context.
