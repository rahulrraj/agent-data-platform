"""Gemini embedding gateway (<Technology><PortName>, §2). Free tier."""

from __future__ import annotations


class GeminiEmbeddingGateway:
    """EmbeddingGateway backed by Gemini's embedding model."""

    def __init__(self, api_key: str, model_id: str = "text-embedding-004") -> None:
        self._api_key = api_key
        self._model_id = model_id

    @property
    def model_name(self) -> str:
        return self._model_id

    def embed(self, texts: list[str]) -> list[list[float]]:
        # Local import: only this adapter needs the google-genai package.
        from google import genai

        client = genai.Client(api_key=self._api_key)
        result = client.models.embed_content(model=self._model_id, contents=texts)
        return [list(e.values) for e in result.embeddings]

    def ping(self) -> None:
        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
