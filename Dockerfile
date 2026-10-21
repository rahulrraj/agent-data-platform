FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir ".[postgres]"

EXPOSE 8000

# The container needs to reach a model: for Ollama, run the model outside
# the container and point at it, e.g. -e OLLAMA_HOST=http://host.docker.internal:11434
# Conversation history: -e CONVERSATION_STORE=postgres -e POSTGRES_DSN=...
CMD ["python", "-m", "agent_api.main"]
