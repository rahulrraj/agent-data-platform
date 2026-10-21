# AGENTS.md

Repo-specific notes for working in this codebase. (General workspace
conventions live in `~/AGENTS.md`.)

## Layout

- `src/agent_api/config.py` — all settings come from env vars; see README table.
- `src/agent_api/tools.py` — every `@tool` lives here and must be registered in `TOOLS`.
- `src/agent_api/agent.py` — `create_agent()`; the only place that picks a model provider.
- `src/agent_api/server.py` — FastAPI app; one `Agent` per `session_id` in `_sessions`.
- `tests/test_api.py` — end-to-end tests; the agent tests drive the real model loop.

## Running / testing

- Install: `pip install -e ".[dev]"` (Python >= 3.10).
- Tests need a model up: `pytest`. The two agent tests take ~1 min on a small local model.
- Default provider is local Ollama (`qwen2.5:1.5b`). Switch with `AGENT_MODEL_PROVIDER=gemini`
  + `GEMINI_API_KEY`; the key check fails fast in `create_agent()`.

## Conventions

- New tools go in `tools.py`: plain function + `@tool` + specific one-line docstring
  (the agent reads it to decide when to call the tool). Keep tools dependency-free.
- Keep the provider switch working: any model-specific code stays inside `create_agent()`.
- `temperature=0, max_tokens=512` on the Ollama provider is deliberate — small models
  go off the rails otherwise (runaway generations, echoed tool definitions).

## Sandbox quirks (this dev VM only)

- `config.py` strips proxy env vars before `ollama` is imported; the sandbox's proxy
  values break the `ollama` package's import-time HTTP client. Harmless elsewhere.
- The sandbox egress proxy blocks Ollama's model registry, so `ollama pull` fails here;
  fetch GGUF weights from HuggingFace and register with `ollama create` instead.
  Outside this sandbox, plain `ollama pull` works.
