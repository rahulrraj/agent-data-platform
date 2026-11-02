"""EmbeddingGateway: turn text into vectors for semantic capability lookup.

Other-system capability (the embedding model lives outside the app), so it is
a gateway port. Adapters: Ollama (local), Gemini (free tier), Bedrock (Titan).
"""

from __future__ import annotations

from typing import Protocol

# Default discovery cutoff (cosine similarity) for embedding models with
# no per-model calibration: the value calibrated for gemini-embedding-001
# (see tools/discover_capability/tool.py). Adapters whose model has its
# own calibrated cutoff declare it via `similarity_threshold` instead.
DEFAULT_SIMILARITY_THRESHOLD = 0.60


class EmbeddingGatewayError(Exception):
    """An embedding operation failed (embed/ping)."""


class EmbeddingGateway(Protocol):
    """Outbound port for text embeddings."""

    @property
    def model_name(self) -> str:
        """ID of the embedding model, for cache keying."""
        ...

    @property
    def similarity_threshold(self) -> float:
        """Minimum cosine similarity for discover_capability to report a
        match for this gateway's embedding model.

        Cosine-similarity scales differ between embedding models, so the
        cutoff is a property of the model, declared here by the adapter
        that owns that model — not a single constant in the tool, and
        not a provider/model branch anywhere else. Adapters with no
        per-model calibration return DEFAULT_SIMILARITY_THRESHOLD.
        """
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per input text, in order."""
        ...

    def ping(self) -> None:
        """Cheap reachability check; raise when the provider is down."""
        ...
