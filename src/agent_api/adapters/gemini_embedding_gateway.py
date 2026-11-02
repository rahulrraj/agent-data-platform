"""Gemini embedding gateway (<Technology><PortName>, §2). Free tier."""

from __future__ import annotations

from agent_api.platform.retry import RetryPolicy, call_with_retry
from agent_api.ports.embedding_gateway import (
    DEFAULT_SIMILARITY_THRESHOLD,
    EmbeddingGatewayError,
)


class GeminiEmbeddingGateway:
    """EmbeddingGateway backed by Gemini's embedding model."""

    def __init__(
        self,
        api_key: str,
        model_id: str = "gemini-embedding-001",
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._api_key = api_key
        self._model_id = model_id
        self._retry = retry_policy or RetryPolicy()

    @property
    def model_name(self) -> str:
        return self._model_id

    @property
    def similarity_threshold(self) -> float:
        # 0.60 is the value calibrated for gemini-embedding-001 (the
        # default model); see tools/discover_capability/tool.py. A
        # different model configured via model_id keeps the default
        # until it gets its own calibration pass.
        return DEFAULT_SIMILARITY_THRESHOLD

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        # google-genai raises APIError subclasses: ServerError for 5xx,
        # ClientError for 4xx (429 = rate limit). Retry 5xx + 429 only.
        try:
            from google.genai import errors as genai_errors
        except ImportError:
            return False
        if isinstance(exc, genai_errors.APIError):
            code = getattr(exc, "code", None)
            return code == 429 or (isinstance(code, int) and 500 <= code < 600)
        return False

    def embed(self, texts: list[str]) -> list[list[float]]:
        # Local import: only this adapter needs the google-genai package.
        from google import genai

        def _embed_once() -> list[list[float]]:
            client = genai.Client(api_key=self._api_key)
            result = client.models.embed_content(model=self._model_id, contents=texts)
            return [list(e.values) for e in result.embeddings]

        try:
            return call_with_retry(
                _embed_once, policy=self._retry, is_transient=self._is_transient
            )
        except EmbeddingGatewayError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingGatewayError(f"gemini embed failed: {exc}") from exc

    def ping(self) -> None:
        """Config-only check: a live embed would burn quota on every probe.

        Readiness therefore means 'key configured' for Gemini embeddings;
        the first real discovery call proves the rest. (Same rationale as
        the Gemini model gateway's config-only ping.)
        """
        if not self._api_key:
            raise EmbeddingGatewayError("GEMINI_API_KEY is not set")
