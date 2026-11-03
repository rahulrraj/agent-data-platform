# Developer guide

Working notes for this repo: layout, conventions, configuration, and
the quirks of the dev VM. The mandatory rules live in `AGENTS.md`;
this doc holds the detail behind them. Keep it current when the code
changes.

## Contents

- [Layout](#layout)
- [Tools](#tools)
- [Data capabilities](#data-capabilities)
- [Providers and frameworks](#providers-and-frameworks)
- [Resilience and operations](#resilience-and-operations)
- [Sandbox quirks (this dev VM only)](#sandbox-quirks-this-dev-vm-only)

## Layout

This repo follows `doc/hexagonal-architecture.md` for *structure* and
`doc/platform-principles.md` for *platform qualities*. The placement
test in §11 of the architecture doc decides where code goes.

- `controllers/` — inbound adapters only (currently HTTP). Parse/validate,
  invoke the use case, map errors to transport status. Never touch adapters.
- `services/` — use cases. `AskAgentService` depends on ports + domain only.
  Port errors become `AskAgentError` subclasses here (§5).
- `domain/` — technology-independent: `conversation.py` (Turn/Conversation),
  `prompts.py` (system prompt builder). Domain imports nothing else.
- `tools/` — one folder per tool plugin (`tools/<name>/tool.py`), auto-discovered
  by `load_tools()`. Top-level rather than under domain/ per ADR 0001
  (`doc/adr/0001-tools-top-level-folder.md`).
- `ports/` — outbound contracts: `ModelGateway` (model + tool loop),
  `ConversationRepository` (app-owned history). Port errors live beside the
  port (`ModelGatewayError`).
- `adapters/` — concrete implementations, named `<Technology><PortName>`:
  `OllamaModelGateway`, `GeminiModelGateway`, `LangGraphOllamaGateway`,
  `LangGraphGeminiGateway` (framework × provider matrix, resolved in the
  composition root from `AGENT_FRAMEWORK` × `AGENT_MODEL_PROVIDER`; see
  ADR 0003). Adapters translate provider failures into port errors.
- `config/` — `settings.py` loads/parses/validates env vars. Nothing else
  reads the environment, except the composition root's proxy strip
  (ADR 0005) — the one sanctioned exception.
- `composition.py` — composition root: the ONLY place that reads Settings
  and picks adapters. No business logic. `main.py` is a thin CLI
  entrypoint over `composition.build_app()`; `controllers/lambda_handler.py`
  imports `build_app` from `composition`, never from `main`.
- `src/ui/` — React chat frontend (Vite). Talks to the API over HTTP/SSE only;
  never imports backend code. See `src/ui/README.md`.
- `registry/` — data capability registry YAML (see `doc/data-capabilities.md`).
- `capability_import/` — offline tooling that drafts registry YAML from specs;
  not part of the runtime path (see `doc/data-capabilities.md`).

Create only the folders the project needs (`workflows/`, `mappers/` were
deliberately skipped). `platform/` holds retry primitives.

## Tools

- New tools: create `tools/<name>/` with a `tool.py` module exposing
  @tool-decorated functions. That's it — `load_tools()` discovers the folder
  automatically, the composition root wires the tools in, and the system
  prompt names them via `build_system_prompt()`. Keep each plugin
  self-contained; if a tool needs outbound I/O later, it must go through
  a core-owned port (§4).
- Factory tools: if a tool needs dependencies (registry, gateways), expose
  `create_tool(deps)` instead of bare functions — see
  `tools/discover_capability/tool.py`. `ToolDeps` carries what's available;
  factory plugins are skipped when `load_tools()` gets no deps.
- Tool functions stay pure and dependency-free; the one-line docstring is
  what the model reads to decide when to call the tool — make it specific.
- `tools/get_current_time` is the one exception to pure tool functions: it
  reads the clock (`datetime.now()`) because reporting the current time is
  its entire job.

## Data capabilities

Don't write per-entity tools. Declare capabilities in
`registry/capabilities.yaml` and let `discover_capability` /
`execute_capability` serve them (`doc/data-capabilities.md`, ADR 0002).

## Providers and frameworks

- New model providers: new adapter implementing `ModelGateway`, wired in
  the composition root. Never branch on provider outside it.
- New agent frameworks: new `*GatewayBase` holding the loop/stream/retry
  mechanics plus per-provider leaves; add to the matrix in the composition
  root (ADR 0003). Tools are defined once as Strands @tool functions and
  translated at the framework boundary — never rewritten per framework.
- `temperature=0, max_tokens=512` on the Ollama adapter is deliberate —
  small models go off the rails otherwise (runaway generations, echoed
  tool defs).
- Default provider is local Ollama (`qwen2.5:1.5b`).
  `AGENT_MODEL_PROVIDER=gemini` + `GEMINI_API_KEY` switches providers with
  no code changes. `AGENT_MODEL_PROVIDER=bedrock` uses AWS Bedrock
  (Strands only, no LangGraph variant yet) with credentials from the
  standard boto3 chain.

## Resilience and operations

- Retries: adapters classify their own transient errors (`_is_transient`);
  shared backoff math lives in `platform/retry.py`. Tune via
  `RETRY_MAX_ATTEMPTS` / `RETRY_BASE_DELAY`.
- Readiness: `GET /api/ready` (model + store). Liveness stays
  `GET /api/health`.
- Conversation store: `CONVERSATION_STORE=memory` (default) or `sqlite`
  (`SQLITE_PATH`, durable across restarts, single host).
- AWS Lambda: `controllers/lambda_handler.py` is a Mangum adapter over the
  same FastAPI app (built via `composition.build_app()`); image in
  `Dockerfile.lambda`. On Lambda, `CONVERSATION_STORE` must be `postgres`
  and `EMBEDDING_CACHE_PATH` must point at `/tmp`. `/api/chat` is
  buffered SSE there (Mangum buffers the whole body) and must be served
  through a Function URL — API Gateway's 29s timeout cannot serve it.
  True incremental streaming is a documented follow-up, not implemented.

## Sandbox quirks (this dev VM only)

- Proxy env vars: the sandbox's proxy values break the `ollama` package's
  import-time HTTP client, so the composition root strips proxy vars
  before lazily importing gateway classes (ADR 0005); adapters never
  touch `os.environ`. Per-call Ollama clients pass `trust_env=False`.
  The same broken values break any `httpx.Client` with `trust_env=True` —
  use `trust_env=False` for localhost calls. Harmless elsewhere.
- The sandbox egress proxy blocks Ollama's model registry, so `ollama pull`
  fails here; fetch GGUF weights from HuggingFace and register with
  `ollama create` instead. Outside this sandbox, plain `ollama pull` works.
