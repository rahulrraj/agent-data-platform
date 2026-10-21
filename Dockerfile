FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir .

EXPOSE 8000

# The container needs to reach a model: for Ollama, run the model on the host
# and point the container at it, e.g. -e OLLAMA_HOST=http://host.docker.internal:11434
CMD ["uvicorn", "agent_api.server:app", "--host", "0.0.0.0", "--port", "8000"]
