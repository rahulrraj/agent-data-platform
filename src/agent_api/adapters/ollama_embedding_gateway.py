"""Ollama embedding gateway (<Technology><PortName>, §2). Local, free."""

from __future__ import annotations

import httpx


class OllamaEmbeddingGateway:
    """EmbeddingGateway backed by Ollama's /api/embed endpoint."""

    def __init__(self, host: str, model_id: str = "nomic-embed-text") -> None:
        self._host = host.rstrip("/")
        self._model_id = model_id

    @property
    def model_name(self) -> str:
        return self._model_id

    def embed(self, texts: list[str]) -> list[list[float]]:
        # trust_env=False: the sandbox's no_proxy values break httpx parsing.
        with httpx.Client(trust_env=False, timeout=60) as client:
            response = client.post(
                f"{self._host}/api/embed",
                json={"model": self._model_id, "input": texts},
            )
            response.raise_for_status()
            return response.json()["embeddings"]

    def ping(self) -> None:
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.get(f"{self._host}/api/tags")
            response.raise_for_status()
