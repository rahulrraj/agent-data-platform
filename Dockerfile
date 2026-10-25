# Base is minor-pinned (3.12-slim). Digest pinning is deferred: digests
# are per-architecture and could not be resolved without docker here;
# a digest pin must cover both amd64 and arm64 manifests, not one arch.
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

# Run as non-root on EKS (m-27): /app is chowned so the default
# embedding cache (embedding_cache.db, relative to /app) stays writable.
# The manifests pin the same UID (10001) in their securityContext.
RUN groupadd -r appuser -g 10001 && useradd -r -u 10001 -g appuser appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# The container needs to reach a model: for Ollama, run the model outside
# the container and point at it, e.g. -e OLLAMA_HOST=http://host.docker.internal:11434
# Conversation history: -e CONVERSATION_STORE=postgres -e POSTGRES_DSN=...
CMD ["python", "-m", "agent_api.main"]
