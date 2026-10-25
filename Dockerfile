FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
# The capability registry must be inside the image: CAPABILITIES_PATH
# defaults to registry/capabilities.yaml and the registry loads eagerly
# in build_app(), so an image without it crashes every pod at startup.
# (Dockerfile.lambda already copies it; this fixes the EKS/manual path.)
COPY registry/ registry/
RUN pip install --no-cache-dir ".[postgres]"

EXPOSE 8000

# The container needs to reach a model: for Ollama, run the model outside
# the container and point at it, e.g. -e OLLAMA_HOST=http://host.docker.internal:11434
# Conversation history: -e CONVERSATION_STORE=postgres -e POSTGRES_DSN=...
CMD ["python", "-m", "agent_api.main"]
