# Data Capabilities

How the agent reaches organization data (Snowflake, SQL Server, REST APIs)
without hardcoded per-entity tools. See ADR 0002 for the decision record.

## Concepts

- **Capability** — one named, parameterized way to reach one data source.
  Declared in `registry/capabilities.yaml`. The model supplies parameter
  *values* only; the query/path is fixed in the registry.
- **Registry** — the YAML file. It is version-controlled configuration:
  adding an entry is a repo edit + review + deploy, never a runtime action.
- **discover_capability** — tool. Semantic search ("customer tier lookup")
  over capability descriptions; returns the single best-matching capability
  with its parameter schema.
- **execute_capability** — tool. Validates parameters, dispatches to the
  executor for the capability's kind, returns JSON.

## Capability granularity

Capabilities are declared at the granularity of the **questions people
ask**, not backend tables (ADR 0004). If answering needs a join or a
fan-out, the API behind the capability does it — the registry holds
`returns_by_customer`, not `accounts_by_customer` + `account_returns` plus
a pipeline. There is no composition engine in this layer: discovery returns
the minimal sufficient set (the one API needed), and the agent loop chains
capabilities only when no single API covers the need.

## Registry format

```yaml
capabilities:
  - id: customer_by_id
    description: "Look up a customer's name, tier, and region by customer ID"
    kind: snowflake            # snowflake | sqlserver | http
    connection: snowflake_analytics   # key into DATA_CONNECTIONS (env JSON)
    mode: read                 # executors refuse anything else
    parameters:
      customer_id:
        type: string           # string | integer | number | boolean
        required: true
        description: "Customer ID, e.g. C-123"
    query: "SELECT name, tier, region FROM dim_customers WHERE customer_id = %(customer_id)s"
```

Kind-specific `spec` keys:

| kind      | spec key  | notes                                                        |
| --------- | --------- | ------------------------------------------------------------ |
| snowflake | `query`   | `%(name)s` placeholders, bound by name (pyformat)            |
| sqlserver | `query`   | `?` placeholders, positional in parameter declaration order  |
| http      | `request` | `method`, `path` (`{name}` from params), optional `fields` allowlist |

HTTP semantics: `GET` puts params not used in the path on the query string;
`POST` sends params as the JSON body. `fields` trims dict responses.

## Connections

`DATA_CONNECTIONS` is a JSON object in the environment mapping connection
names to configs. Secrets live here, never in the registry file:

```json
{
  "snowflake_analytics": {
    "account": "...", "user": "...", "private_key": "-----BEGIN...",
    "warehouse": "...", "database": "...", "schema": "...", "role": "..."
  },
  "sqlserver_sales": {
    "server": "...", "database": "...",
    "username": "...", "password": "..."
  },
  "orders_api": {
    "base_url": "https://api.example.com", "api_key": "..."
  }
}
```

Snowflake accepts `private_key` (preferred) or `password`. SQL Server accepts
`username`/`password` or `"trusted_connection": true`.

## Flow

1. User: "What tier is customer C-123?"
2. Model → `discover_capability("customer tier lookup")` →
   `[{"id": "customer_by_id", "parameters": {"customer_id": {...}}}]`
3. Model → `execute_capability("customer_by_id", {"customer_id": "C-123"})` →
   `{"rows": [{"name": "...", "tier": "Gold"}], "truncated": false}`
4. Model answers.

## Generating from Swagger

Hand-writing entries is fine for a few; for a whole API, generate a draft:

    python -m agent_api.capability_import --from swagger \
        --spec openapi.json --connection orders_api --out draft.yaml

OpenAPI 3.x and Swagger 2.0; GET operations only (the platform is
read-only — anything else is listed as skipped, not guessed); query + path
parameters with local `$ref`s resolved. Output is a **draft**: descriptions
come from the spec's summaries, so rewrite them as user needs (ADR 0004)
and set `fields` where you want trimmed responses, then merge into the
registry by hand. The registry stays reviewed code — the generator never
edits it. Snowflake-table import will plug into the same module later
(`--from snowflake`).

## What the model sees

Concise and complete — nothing more, nothing less. The model gets: the
system prompt (tool names + one-line behavior notes), each tool's
docstring, and discovery results (id, description, parameter schemas with
types, required flags, and descriptions). It never sees queries, URLs,
credentials, or connection details. Parameter descriptions are the
contract the model builds requests from — write them so a value can be
lifted straight out of a user prompt. If a required value isn't in the
prompt, the model should ask, not guess.

## Embedding cache

At thousands of capabilities, re-embedding every description on each
restart is a slow cold start and real API cost. Description vectors are
cached in SQLite (`EMBEDDING_CACHE_PATH`, default `embedding_cache.db`),
keyed by capability id + embedding model, with a sha256 of the
description text. Only new or edited descriptions are re-embedded;
switching embedding models invalidates everything (vectors from different
models aren't comparable). The cache is best-effort: if it's missing or
broken, discovery falls back to embedding everything in memory, and a
failed embedding backend still reports "temporarily unavailable" instead
of guessing.

## Prerequisites

- The embedding model must exist in the provider: `nomic-embed-text` in
  Ollama (`OLLAMA_EMBED_MODEL` overrides), or the Gemini embedding model
  for the `gemini` provider. Descriptions are embedded once at startup.

If nothing matches (below the similarity threshold), discovery says so
instead of guessing. If parameters are missing/wrong-typed/unknown,
execution returns a clean error message — never a traceback.

## Guardrails

- **No raw SQL / no raw URLs from the model.** Ever. Queries and paths are
  fixed in the registry; the model fills values.
- **Read-only.** `mode: read` is enforced by every executor.
- **Minimal fields.** Select only what the answer needs — results enter the
  model's context window.
- **Row caps.** SQL executors return at most 50 rows and flag `truncated`.
- **Retries.** Executors retry transient failures (connection drops,
  429/5xx) with backoff; bad SQL / 4xx fail fast.
- **Least-privilege credentials.** Service accounts should be read-only and
  scoped to the schemas they need.
- **Audit.** Log every execution: capability id, parameters, timestamp.
  (Structured audit logging is planned — today this means application logs.)

## Access

The registry is the access control plane: whoever can edit
`registry/capabilities.yaml` decides what data the agent can reach. That is
by design — and why the file is reviewed code, not runtime configuration.

v1 has **no per-user authorization**: the agent acts with the service's
permissions, so every user sees what the service account can see. Per-user
scoping (e.g. `allowed_roles` per capability + request identity) is the
planned follow-up; the validation seam in `execute_capability` is where the
check will live.
