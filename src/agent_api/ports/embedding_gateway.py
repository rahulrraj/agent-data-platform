"""EmbeddingGateway: turn text into vectors for semantic capability lookup.

Other-system capability (the embedding model lives outside the app), so it is
a gateway port. Adapters: Ollama (local), Gemini (free tier), Bedrock (Titan).
"""

from __future__ import annotations

from typing import Protocol


class EmbeddingGatewayError(Exception):
    """An embedding operation failed (embed/ping)."""


class EmbeddingGateway(Protocol):
    """Outbound port for text embeddings."""

    @property
    def model_name(self) -> str:
        """ID of the embedding model, for cache keying."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per input text, in order."""
        ...

    def ping(self) -> None:
        """Cheap reachability check; raise when the provider is down."""
        ...
