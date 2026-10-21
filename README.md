# Agent API

A minimal HTTP API that invokes a [Strands](https://strandsagents.com) agent
with a few tools. Structure mirrors the gemini-agent backend: tools and the
agent factory live in `agent_api/`, the FastAPI service in `agent_api/server.py`.

```
src/agent_api/
  config.py   environment-based settings (provider, model, host, port)
  tools.py    @tool functions (calculate, get_current_time, word_count)
  agent.py    create_agent() — wires model + prompt + tools
  server.py   FastAPI app
tests/
  test_api.py end-to-end tests (drive the real agent loop)
```

## Quickstart

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"

# 1. Start a model. Default is local Ollama (free, no key):
ollama serve & ollama pull qwen2.5:1.5b
# …or use Gemini's free tier:
export AGENT_MODEL_PROVIDER=gemini GEMINI_API_KEY=<key>   # https://aistudio.google.com/apikey

# 2. Run the API
.venv/bin/python -m agent_api.server        # http://localhost:8000
# or: uvicorn agent_api.server:app --port 8000

# 3. Run the tests (needs the model up; the agent tests take ~1 min)
.venv/bin/pytest
```

## Endpoints

| Method | Path         | Description                                              |
| ------ | ------------ | -------------------------------------------------------- |
| GET    | /api/health  | `{"status": "ok", "provider": ..., "model": ...}`        |
| POST   | /api/chat    | Streams the reply as Server-Sent Events (text + tool use)|
| POST   | /api/ask     | Returns the full reply as JSON: `{reply, tools_used}`    |

Both POST endpoints take `{"message": "...", "session_id": "..."}`.
Each `session_id` gets its own agent, so conversation history persists per client.

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
