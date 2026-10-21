# Agent Data Platform

An agent platform: one HTTP API that invokes an AI agent, a React chat UI,
and a registry that connects the agent to organization data (Snowflake,
SQL Server, REST APIs) discovered at runtime. Switch the agent framework
(Strands / LangGraph) and the model (Ollama / Gemini) with env vars — the
service never knows which is running. The agent answers questions with tools,
including organization data discovered by meaning from the capability registry.

## System overview

```mermaid
flowchart TB
    Browser([User in browser<br/>src/ui — React chat]) --> HTTP["<b>controllers/http.py</b><br/>POST /api/ask · POST /api/chat (SSE)<br/>GET /api/health · GET /api/ready"]
    HTTP --> Svc["<b>services/ask_agent.py</b><br/>AskAgentService — the 'ask the agent' use case"]
    Svc --> CR["ports/<b>ConversationRepository</b><br/>load · save · ping"]
    Svc --> MG["ports/<b>ModelGateway</b><br/>generate · stream · ping"]
    CR --> CRA["adapters/<br/>InMemoryConversationRepository<br/>SqliteConversationRepository"]
    MG --> FW{"AGENT_FRAMEWORK"}
    FW -- strands --> SB["<b>strands_base.py</b><br/>Strands agent loop · retries<br/>Strands events → StreamChunk"]
    FW -- langgraph --> LB["<b>langgraph_base.py</b><br/>LangGraph ReAct loop · retries<br/>LangChain events → StreamChunk"]
    SB --> PV1{"AGENT_MODEL_PROVIDER"}
    LB --> PV2{"AGENT_MODEL_PROVIDER"}
    PV1 -- ollama --> SO["OllamaModelGateway"]
    PV1 -- gemini --> SG["GeminiModelGateway"]
    PV2 -- ollama --> LO["LangGraphOllamaGateway"]
    PV2 -- gemini --> LGG["LangGraphGeminiGateway"]
    SO & SG & LO & LGG --> Tools["<b>tools/</b> — one folder per plugin<br/>calculate · get_current_time · word_count<br/>discover_capability · execute_capability"]
    Tools --> CAP["ports/<b>CapabilityRepository</b><br/>reads registry/capabilities.yaml"]
    Tools --> EXE["ports/<b>CapabilityExecutor</b><br/>one adapter per kind"]
    EXE --> K{"kind"}
    K -- snowflake --> K1["SnowflakeCapabilityExecutor"] --> SF[(Snowflake)]
    K -- sqlserver --> K2["SqlServerCapabilityExecutor"] --> SS[(SQL Server)]
    K -- http --> K3["HttpCapabilityExecutor"] --> RA[(REST API)]
```

## How a question is answered

```mermaid
sequenceDiagram
    participant U as User
    participant H as HTTP controller
    participant S as AskAgentService
    participant R as ConversationRepository
    participant G as ModelGateway
    participant M as Model

    U->>H: POST /api/ask with message + session_id
    H->>S: ask(session_id, message)
    S->>R: load(session_id)
    R-->>S: Conversation(history)
    S->>G: generate(conversation)
    G->>M: system prompt + history + tools
    M-->>G: tool_call calculate(expression="6 * 7")
    G->>G: run tool, feed result back
    M-->>G: final text
    G-->>S: Turn(text, tool_uses)
    S->>R: save(conversation + new turns)
    S-->>H: AgentReply
    H-->>U: reply + tools_used + session_id
```

`/api/chat` is the same flow, but the gateway streams `StreamChunk`s
(text / tool / error) as Server-Sent Events instead of one reply.

## How data capabilities are discovered and run

No per-entity tools are hardcoded. Capabilities live in
`registry/capabilities.yaml`; at runtime the agent finds the right one by
meaning, then runs it. The model supplies parameter **values** only — queries
and URLs are fixed in the registry.

```mermaid
sequenceDiagram
    participant M as Model
    participant D as discover_capability
    participant E as EmbeddingGateway
    participant R as CapabilityRepository
    participant X as execute_capability
    participant W as CapabilityExecutor
    participant DB as Snowflake / SQL Server / REST API

    Note over M: What tier is customer C-123?
    M->>D: discover_capability("customer tier lookup")
    D->>E: embed("customer tier lookup")
    E-->>D: vector
    D->>D: cosine similarity against<br/>pre-embedded capability descriptions
    D-->>M: id=customer_by_id + parameter schema
    M->>X: execute_capability("customer_by_id",<br/>{"customer_id": "C-123"})
    X->>R: get("customer_by_id")
    R-->>X: kind=snowflake, fixed parameterized query
    X->>X: validate params<br/>required · types · reject unknowns
    X->>W: execute(capability, params)
    W->>DB: run query with bound values
    DB-->>W: rows
    W-->>X: rows as JSON (max 50, truncated flag)
    X-->>M: JSON result
    Note over M: Customer C-123 is Gold tier.
```

If nothing matches, discovery says so instead of guessing. If parameters are
missing, mistyped, or unknown, execution returns a clean error — never a
traceback. The registry file is version-controlled: adding a capability is a
repo edit + review + deploy.

Description vectors are embedded once and cached in SQLite
(`EMBEDDING_CACHE_PATH`), so a restart doesn't re-embed thousands of
descriptions — only new or edited ones. To draft entries from a
Swagger/OpenAPI spec: `python -m agent_api.capability_import --from swagger
--spec openapi.json --connection <name> --out draft.yaml`. Output is a draft
for review; the `skills/capability-generator` skill documents the review
checklist (descriptions as user needs, example values, merge, test).

```yaml
# registry/capabilities.yaml — one entry exposes one dataset
- id: customer_by_id
  description: "Look up a customer's name, tier, and region by customer ID"
  kind: snowflake            # snowflake | sqlserver | http
  connection: snowflake_analytics   # key into DATA_CONNECTIONS (env JSON)
  mode: read                 # executors refuse anything else
  parameters:
    customer_id: {type: string, required: true}
  query: "SELECT name, tier, region FROM dim_customers WHERE customer_id = %(customer_id)s"
```

## Layers and dependency rules

```mermaid
flowchart TB
    CT["<b>controllers/</b> — inbound<br/>parse input, call the use case,<br/>map errors to HTTP status"]
    SV["<b>services/</b> — use cases<br/>AskAgentService depends on<br/>ports + domain only"]
    PT["<b>ports/</b> — contracts<br/>ModelGateway · ConversationRepository<br/>CapabilityRepository · CapabilityExecutor · EmbeddingGateway"]
    DM["<b>domain/</b> — pure logic<br/>conversation · capability ·<br/>prompt builder · cosine math"]
    TL["<b>tools/</b> — plugins<br/>one folder per tool;<br/>bare functions or create_tool(deps)"]
    AD["<b>adapters/</b> — implement ports<br/>gateways · repositories · executors<br/>all SDKs live here"]
    PL["<b>platform/</b> — shared technical capabilities<br/>retry with backoff + jitter"]
    MN(["<b>main.py</b> — composition root<br/>ONLY place that reads config<br/>ONLY place that picks adapters"])

    CT --> SV --> PT
    TL --> PT
    SV --> DM
    AD -. implements .-> PT
    AD --> PL
    MN -. wires .-> CT
    MN -. wires .-> AD
    MN -. wires .-> TL
```

Rules: dependencies point one way (`controllers → services → ports`,
adapters implement ports). Tools never touch SDKs — data access goes through
a core-owned port. `config/settings.py` is the only place that reads the
environment.

## Switching framework and model

`main.py` resolves the framework × provider matrix. The service only ever
sees a `ModelGateway`.

|                      | `ollama` (local)      | `gemini` (free tier)   |
| -------------------- | --------------------- | ---------------------- |
| `strands` (default)  | OllamaModelGateway    | GeminiModelGateway     |
| `langgraph`          | LangGraphOllamaGateway| LangGraphGeminiGateway |

```bash
AGENT_FRAMEWORK=langgraph AGENT_MODEL_PROVIDER=gemini GEMINI_API_KEY=<key> python -m agent_api.main
```

Tools are defined once as Strands `@tool` functions; the LangGraph adapter
translates them to LangChain tools at the framework boundary. Embeddings stay
on the provider path either way.

## Resilience

- Transient failures (dropped connections, 429/5xx) retry with exponential
  backoff + jitter (`platform/retry.py`); bad input fails fast.
- `GET /api/health` is liveness; `GET /api/ready` is readiness (model
  reachable, store readable) for load balancers.
- Gateways are stateless per call; history durability comes from the store
  adapter (`memory` default, `sqlite` for restart-proof single host,
  Postgres planned for multi-instance).

## Run it

```bash
pip install -e ".[dev]"
python -m agent_api.main
curl -X POST localhost:8000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"message": "What is 6 * 7? Use the calculate tool.", "session_id": "demo"}'
```

## Web UI

React chat frontend in `src/ui/` — ChatGPT-style layout, Claude-style warmth,
Gemini-style accents. Streams replies token-by-token over `/api/chat`.

```bash
cd src/ui && npm install && npm run dev    # http://localhost:5173
```

`/api/*` is proxied to the API in dev; set `VITE_API_URL` for a deployed
build. See `src/ui/README.md`.

## Deploy on Kubernetes

Same manifests on EKS, GKE, AKS, or on-prem — see `deploy/k8s/README.md`.
Two Deployments (API + nginx-served UI), one Ingress (`/api/*` → API),
ConfigMap + Secret for env, and a Postgres StatefulSet for the
conversation store (swap for a managed database in prod). The API image is
built from the repo-root `Dockerfile`; the UI image from `src/ui/Dockerfile`.

## Configure

| Variable               | Default                  | Purpose                                              |
| ---------------------- | ------------------------ | ---------------------------------------------------- |
| AGENT_FRAMEWORK        | `strands`                | `strands` or `langgraph` (ADR 0003)                  |
| AGENT_MODEL_PROVIDER   | `ollama`                 | `ollama` (local, free) or `gemini`                   |
| OLLAMA_HOST            | `http://localhost:11434` | Ollama server address                                |
| OLLAMA_MODEL           | `qwen2.5:1.5b`           | Chat model for `ollama`                              |
| GEMINI_MODEL           | `gemini-2.5-flash`       | Chat model for `gemini`                              |
| GEMINI_API_KEY         | —                        | Required when provider is `gemini`                   |
| OLLAMA_EMBED_MODEL     | `nomic-embed-text`       | Embedding model for capability discovery             |
| GEMINI_EMBED_MODEL     | `text-embedding-004`     | Embedding model when provider is `gemini`            |
| RETRY_MAX_ATTEMPTS     | `3`                      | Retry attempts for transient failures                |
| RETRY_BASE_DELAY       | `0.5`                    | Base backoff delay in seconds (jittered)             |
| CONVERSATION_STORE     | `memory`                 | `memory` (process-local), `sqlite`, or `postgres`        |
| SQLITE_PATH            | `conversations.db`       | SQLite file when store is `sqlite`                   |
| POSTGRES_DSN           | —                        | e.g. `postgres://user:pass@host:5432/db` (store `postgres`) |
| EMBEDDING_CACHE_PATH   | `embedding_cache.db`     | SQLite file caching description vectors              |
| CAPABILITIES_PATH      | `registry/capabilities.yaml` | Data capability registry                         |
| DATA_CONNECTIONS       | `{}`                     | JSON: named data-source connections (see below)      |
| PORT                   | `8000`                   | API port                                             |

`DATA_CONNECTIONS` maps connection names (as referenced by the registry) to
configs — secrets live here, never in the YAML:

```json
{"snowflake_analytics": {"account": "...", "user": "...", "private_key": "...",
 "warehouse": "...", "database": "..."},
 "orders_api": {"base_url": "https://api.example.com", "api_key": "..."}}
```

## API

| Method | Path         | Description                                                        |
| ------ | ------------ | ------------------------------------------------------------------ |
| GET    | /api/health  | `{"status": "ok", "model": ...}` (liveness)                        |
| GET    | /api/ready   | `{"status": "ready"}` or 503 `{"status": "not_ready", ...}`         |
| POST   | /api/ask     | `{message, session_id?}` → `{reply, tools_used, session_id}`        |
| POST   | /api/chat    | Streams the reply as Server-Sent Events (text/tool/error chunks)   |

Errors: invalid input → 400; model/data failure → 502 with a clean message.

## Project map

```text
src/agent_api/
  main.py                 composition root: reads config, wires everything, starts
  config/settings.py      load/parse/validate settings (env vars)
  controllers/http.py     inbound HTTP adapter: routes, validation, error mapping
  services/ask_agent.py   the "ask the agent" use case (depends only on ports)
  domain/                 technology-independent core: conversation, capability,
                          prompt builder, cosine similarity
  tools/                  one folder per tool plugin (auto-discovered, see ADR 0001)
                          factory plugins expose create_tool(deps) for injection
  ports/                  outbound contracts: model_gateway, conversation_repository,
                          capability_repository, capability_executor,
                          embedding_gateway, embedding_cache
  adapters/               port implementations: strands/langgraph gateways (× ollama/gemini),
                          memory/sqlite repos, yaml registry, http/snowflake/sqlserver
                          executors, ollama/gemini embeddings, sqlite embedding cache
  capability_import/      spec → draft capability YAML (swagger now, snowflake planned)
  platform/               shared technical capabilities: retry with backoff
registry/capabilities.yaml
src/ui/                      React chat frontend (see src/ui/README.md)
skills/capability-generator/ project skill: generate → review → merge → test
doc/
  hexagonal-architecture.md
  platform-principles.md  cloud-agnostic, resilient, multi-runtime, model-agnostic
  data-capabilities.md    registry-driven org data access (Snowflake/SQL Server/HTTP)
  adr/                    architecture decision records
```

## Docs

| Doc | What it is |
| --- | ---------- |
| `doc/hexagonal-architecture.md` | Structure rules: layers, ports, dependency directions (from the original Drive doc) |
| `doc/platform-principles.md` | Platform qualities: cloud-agnostic, resilient, multi-runtime, model-agnostic + conformance checklist |
| `doc/data-capabilities.md` | Registry format, discovery/execution flow, guardrails, access model |
| `doc/agent-lifecycle.md` | ADLC: stages, gates, and the ship checklist |
| `doc/guardrails.md` | Guardrail catalog — enforced vs. planned, honestly marked |
| `doc/approvals.md` | Approval matrix, what counts as approval, emergency path |
| `doc/adr/` | Decision records: 0001 tools folder · 0002 capability registry · 0003 dual frameworks · 0004 no composites |

## Principles in one line each

- **Hexagonal**: core knows nothing outbound; adapters implement ports; `main.py` wires everything.
- **Model-agnostic**: every model call goes through `ModelGateway`; provider switch is one env var.
- **Framework-agnostic**: Strands and LangGraph both implement `ModelGateway`; tools are defined once.
- **Data by configuration**: new dataset = one YAML entry + one named connection. No code.
- **Least privilege**: read-only executors, minimal columns, 50-row caps, service credentials scoped down.
- **Reviewed, not runtime**: the registry is code — edits need repo access and review.
