# Data Capabilities

How the agent reaches organization data (Snowflake, SQL Server, REST APIs)
without hardcoded per-entity tools. See ADR 0002 for the decision record.

## Contents

- [Concepts](#concepts)
- [Capability granularity](#capability-granularity)
- [Registry format](#registry-format)
  - [Trimming large HTTP responses](#trimming-large-http-responses)
  - [Forwarding the user's token (HTTP only)](#forwarding-the-users-token-http-only)
- [Connections](#connections)
- [Flow](#flow)
- [Generating from Swagger](#generating-from-swagger)
- [What the model sees](#what-the-model-sees)
- [Embedding cache](#embedding-cache)
- [Prerequisites](#prerequisites)
- [Guardrails](#guardrails)
- [Access](#access)

## Concepts

- **Capability** — one named, parameterized way to reach one data source.
  Declared in `registry/capabilities.yaml`. The model supplies parameter
  *values* only; the query/path is fixed in the registry.
- **Registry** — the YAML file. It is version-controlled configuration:
  adding an entry is a repo edit + review + deploy, never a runtime action.
- **discover_capability** — tool. Semantic search ("customer tier lookup")
  over capability descriptions (plus parameter descriptions); returns the
  single best-matching capability with its parameter schema and score —
  or the top two with scores when they are within the margin (δ = 0.05),
  so a near-tie is the model's choice, not a forced guess.
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
`POST` sends params as the JSON body. `fields` trims dict responses and
dict items inside list responses; list results are capped at 50 by
default with a `truncated` flag, mirroring the SQL row cap. A 204/empty
body yields `{"data": null}` and a non-JSON 200 body is returned as text,
not a crash.
Path parameter values are percent-encoded, and a `/` in a value is refused
unless the capability opts that parameter into `multi_segment_params`.
Connection targets are validated at executor construction: `base_url`
must be https and must not be a loopback/private/reserved host unless the
connection opts in with `allow_private_base_url` (local dev only).

### Trimming large HTTP responses

Three optional top-level registry keys control how list results are
trimmed before they enter the model's context (HTTP capabilities only;
the SQL row cap stays at 50 and is unaffected):

```yaml
  - id: price_history
    description: "Daily closing prices for a symbol, most recent first"
    kind: http
    connection: market_api
    list_paths: ["history.prices"]  # dotted paths to nested lists
    keep: last                      # first | last (default: first)
    max_results: 200                # 1..500 (default: 50)
    request:
      method: GET
      path: "/prices/{symbol}"
```

- `list_paths`: dotted chains of dict keys from the response root to a
  nested list (e.g. a time-series list inside the payload). Trimming
  applies at each listed path. When no paths are given, the top-level
  list is trimmed instead, as before; a top-level list is always
  capped. A path that does not resolve (missing key, non-dict
  intermediate, non-list at the end) is left untouched, not an error.
- `keep`: `first` keeps the oldest rows (the default, preserving
  current behavior); `last` keeps the most recent rows — the
  time-series case, where the newest data matters most.
- `max_results`: the cap applied at each trimmed list. The default is
  50; a capability may opt in to a higher cap, up to 500. The 500
  ceiling is an opt-in per capability, not a new default.

`truncated` is true whenever anything was trimmed — at the top level
or at any listed path. When `list_paths` are configured, the flag is
always present on the result (even when nothing was trimmed); for a
plain dict response without `list_paths` it is omitted, as before.
`fields` does not reach nested lists: it trims only the top-level dict
or top-level list items.

Invalid values are rejected when the registry loads (fail fast at
startup, naming the capability): `max_results` outside 1..500 or not an
integer, `keep` other than `first`/`last`, or `list_paths` not a list of
non-empty strings.

### Forwarding the user's token (HTTP only)

One optional top-level registry key, `forward_user_token: true`, makes
an HTTP capability send the calling user's bearer token to the
downstream API as its `Authorization` header, instead of a static
service credential (ADR 0008). The token comes from the per-turn
request context — never from tool arguments or conversation history,
so the model never sees it — and is sent only when a token is present
for the turn. A non-boolean value is rejected when the registry loads,
naming the capability. The key is validated-but-ignored for SQL
kinds, which keep service credentials only.

A capability with `forward_user_token: true` must not use a connection
that defines a static `api_key`: two identities for one call is a
startup config error (checked when the app is composed, naming the
capability and the connection), and the executor refuses the call
rather than sending either identity if both are somehow present. A
downstream 401/403 surfaces as a capability error with no token
details and no upstream body.

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
   `{"id": "customer_by_id", "score": 0.87, "parameters": {"customer_id": {...}}}`
   (a single object per ADR 0004, not a list)
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

Placement (m-3): `capability_import` is offline registry tooling, not a
runtime layer. It converts a spec into draft YAML and touches no port or
adapter; it never edits the live registry. That is why it sits outside
the controllers → services → ports layering: the placement test applies
to runtime code, and this code never runs in the app.

The generator loop, end to end: generate → review (required, as above)
→ `--validate` (round-trips the draft through the registry loader and
reports id collisions against the live registry) → merge → test. `fields`
are filled from a declared 200-response object schema when one exists.
Descriptions containing instruction-like phrasing from the spec are
flagged as warnings — spec text reaches the model verbatim through
discovery, so such a description must be rewritten before merging, never
merged as-is.

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
restart is a slow cold start and real API cost. Indexed texts (description
+ parameter descriptions) are embedded lazily on first discovery — never
at startup, so a down embedding backend cannot block boot — and cached
in SQLite (`EMBEDDING_CACHE_PATH`, default `embedding_cache.db`), keyed
by capability id + embedding model, with a sha256 of the indexed text.
Only new or edited texts are re-embedded; switching embedding models
invalidates everything (vectors from different models aren't comparable).
Each row records its vector dimension, and discovery re-embeds rather
than ranking when cached dimensions disagree or a row's recorded
dimension disagrees with its vector; rows for models no longer in use
are pruned. The cache is best-effort: if it's missing or broken,
discovery falls back to embedding everything in memory, and a failed
embedding backend still reports "temporarily unavailable" instead
of guessing.

## Prerequisites

- The embedding model must exist in the provider: `nomic-embed-text` in
  Ollama (`OLLAMA_EMBED_MODEL` overrides), or the Gemini embedding model
  for the `gemini` provider. Indexed texts are embedded lazily on first
  use and cached, as above.

If nothing matches (below the similarity threshold), discovery says so
instead of guessing. If parameters are missing/wrong-typed/unknown,
execution returns a clean error message — never a traceback.

## Guardrails

- **No raw SQL / no raw URLs from the model.** Ever. Queries and paths are
  fixed in the registry; the model fills values.
- **Read-only.** `mode: read` is enforced by every executor (SQL
  statements must start with SELECT/WITH; HTTP read capabilities are
  GET-only).
- **Minimal fields.** Select only what the answer needs — results enter the
  model's context window.
- **Row caps.** SQL executors return at most 50 rows and flag `truncated`;
  HTTP list results get the same 50 default and flag, and an HTTP
  capability may opt in (per capability, validated at registry load) to
  a cap up to 500 and to trimming nested lists via `list_paths` /
  `keep: last` — see "Trimming large HTTP responses" above.
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
