# AGENTS.md

Repo-specific notes for working in this codebase. (General workspace
conventions live in `~/AGENTS.md`.)

## Architecture

This repo follows `doc/architecture-reference.md` (hexagonal/clean
architecture; copied from the `# Architecture Reference` Drive doc — the Drive
copy is the original). The placement test in §11 decides where code goes;
the one-way dependency rule in §3 is mandatory:

```text
controllers/ -> services/ -> ports/        (adapters implement ports)
```

- `controllers/` — inbound adapters only (currently HTTP). Parse/validate,
  invoke the use case, map errors to transport status. Never touch adapters.
- `services/` — use cases. `AskAgentService` depends on ports + domain only.
  Port errors become `AskAgentError` subclasses here (§5).
- `domain/` — technology-independent: `conversation.py` (Turn/Conversation),
  `tools.py` (pure @tool functions — no I/O), `prompts.py` (system prompt).
  Domain imports nothing else.
- `ports/` — outbound contracts: `ModelGateway` (model + tool loop),
  `ConversationRepository` (app-owned history). Port errors live beside the
  port (`ModelGatewayError`).
- `adapters/` — concrete implementations, named `<Technology><PortName>`:
  `OllamaModelGateway`, `GeminiModelGateway`, `InMemoryConversationRepository`.
  Adapters translate provider failures into port errors.
- `config/` — `settings.py` loads/parses/validates env vars. Nothing else
  reads the environment.
- `main.py` — composition root: the ONLY place that reads Settings and picks
  adapters. No business logic.

Create only the folders the project needs (`workflows/`, `mappers/`,
`platform/` were deliberately skipped). Record deliberate exceptions as a
short ADR instead of silently changing the pattern (§12.10).

## Running / testing

- Install: `pip install -e ".[dev]"` (Python >= 3.10). Run: `python -m agent_api.main`.
- Tests: `pytest`. Service tests use fake ports, controller tests use a fake
  service — neither needs a model. The adapter test hits real Ollama and
  skips itself when the server is down.
- Default provider is local Ollama (`qwen2.5:1.5b`). `AGENT_MODEL_PROVIDER=gemini`
  + `GEMINI_API_KEY` switches providers with no code changes.

## Conventions

- New tools: pure function + `@tool` + specific one-line docstring in
  `domain/tools.py`, registered in `TOOLS`. Keep tools dependency-free.
- New model providers: new adapter implementing `ModelGateway`, wired in
  `main.build_gateway()`. Never branch on provider outside `main`.
- `temperature=0, max_tokens=512` on the Ollama adapter is deliberate — small
  models go off the rails otherwise (runaway generations, echoed tool defs).

## Sandbox quirks (this dev VM only)

- `adapters/ollama_model_gateway.py` strips proxy env vars at import time; the
  sandbox's proxy values break the `ollama` package's import-time HTTP client.
  The same broken values break any `httpx.Client` with `trust_env=True` —
  use `trust_env=False` for localhost calls. Harmless elsewhere.
- The sandbox egress proxy blocks Ollama's model registry, so `ollama pull`
  fails here; fetch GGUF weights from HuggingFace and register with
  `ollama create` instead. Outside this sandbox, plain `ollama pull` works.
