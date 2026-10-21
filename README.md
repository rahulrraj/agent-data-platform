# Agent API

A minimal HTTP API that invokes a [Strands](https://strandsagents.com) agent
with a few tools. The code follows `doc/architecture-reference.md`
(hexagonal/clean architecture with DDD tactical patterns — copied from the
`# Architecture Reference` doc in Drive).

```
src/agent_api/
  main.py                 composition root: reads config, wires everything, starts
  config/settings.py      load/parse/validate settings (env vars)
  controllers/http.py     inbound HTTP adapter: routes, validation, error mapping
  services/ask_agent.py   the "ask the agent" use case (depends only on ports)
  domain/                 technology-independent core: conversation, prompt
  tools/                  one folder per tool plugin (auto-discovered, see ADR 0001)
  ports/                  outbound contracts: model_gateway, conversation_repository
  adapters/               port implementations: ollama/gemini gateways, in-memory repo
doc/
  architecture-reference.md
tests/
  services/     use-case tests with fake ports (no model needed)
  controllers/  HTTP boundary tests with a fake service
  adapters/     integration test against a real Ollama server (skipped if down)
```

Dependency direction: `controllers -> services -> ports`, `adapters -> ports`.
Only `main.py` reads configuration; only `main.py` picks concrete adapters.

## Quickstart

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"

# 1. Start a model. Default is local Ollama (free, no key):
ollama serve & ollama pull qwen2.5:1.5b
# …or use Gemini's free tier:
export AGENT_MODEL_PROVIDER=gemini GEMINI_API_KEY=<key>   # https://aistudio.google.com/apikey

# 2. Run the API
.venv/bin/python -m agent_api.main        # http://localhost:8000

# 3. Run the tests (service + controller tests need no model)
.venv/bin/pytest
```

## Endpoints

| Method | Path         | Description                                              |
| ------ | ------------ | -------------------------------------------------------- |
| GET    | /api/health  | `{"status": "ok", "model": ...}`                         |
| POST   | /api/chat    | Streams the reply as Server-Sent Events (text/tool/error)|
| POST   | /api/ask     | Returns the full reply as JSON: `{reply, tools_used}`    |

Both POST endpoints take `{"message": "...", "session_id": "..."}`.
Conversation history is owned by the app via the `ConversationRepository`
port (in-memory adapter by default — swap for a database without touching
the service).

```bash
curl -X POST localhost:8000/api/ask -H 'Content-Type: application/json' \
  -d '{"message": "What is 6 * 7? Use the calculate tool.", "session_id": "rahul"}'
# -> {"reply": "The result of 6 * 7 is 42.", "tools_used": ["calculate"], ...}
```

## Configuration

| Variable               | Default                  | Description                              |
| ---------------------- | ------------------------ | ---------------------------------------- |
| AGENT_MODEL_PROVIDER   | `ollama`                 | `ollama` (local, free) or `gemini`       |
| OLLAMA_HOST            | `http://localhost:11434` | Ollama server address                    |
| OLLAMA_MODEL           | `qwen2.5:1.5b`           | Any Ollama model tag                     |
| GEMINI_MODEL           | `gemini-2.5-flash`       | Gemini model id                          |
| GEMINI_API_KEY         | —                        | Required when provider is `gemini`       |
| PORT                   | `8000`                   | API port                                 |

## Docker

```bash
docker build -t agent-api .
docker run -p 8000:8000 -e OLLAMA_HOST=http://host.docker.internal:11434 agent-api
```
