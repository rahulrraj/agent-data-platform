"""Ollama embedding gateway (<Technology><PortName>, §2). Local, free."""

from __future__ import annotations

import httpx

from agent_api.platform.retry import RetryPolicy, call_with_retry
from agent_api.ports.embedding_gateway import EmbeddingGatewayError


class OllamaEmbeddingGateway:
    """EmbeddingGateway backed by Ollama's /api/embed endpoint."""

    def __init__(
        self,
        host: str,
        model_id: str = "nomic-embed-text",
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._host = host.rstrip("/")
        self._model_id = model_id
        self._retry = retry_policy or RetryPolicy()

    @property
    def model_name(self) -> str:
        return self._model_id

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        if isinstance(
            exc,
            (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError),
        ):
            return True
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        return status == 429 or (isinstance(status, int) and 500 <= status < 600)

    def embed(self, texts: list[str]) -> list[list[float]]:
        def _embed_once() -> list[list[float]]:
            # trust_env=False: the sandbox's no_proxy values break httpx parsing.
            with httpx.Client(trust_env=False, timeout=60) as client:
                response = client.post(
                    f"{self._host}/api/embed",
                    json={"model": self._model_id, "input": texts},
                )
                response.raise_for_status()
                return response.json()["embeddings"]

        try:
            return call_with_retry(
                _embed_once, policy=self._retry, is_transient=self._is_transient
            )
        except EmbeddingGatewayError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingGatewayError(f"ollama embed failed: {exc}") from exc

    def ping(self) -> None:
        """Tags check (no quota cost for a local model) + model presence."""
        try:
            with httpx.Client(trust_env=False, timeout=5) as client:
                response = client.get(f"{self._host}/api/tags")
                response.raise_for_status()
                names = [m.get("name", "") for m in response.json().get("models", [])]
            if self._model_id not in names and not any(
                n.rsplit(":", 1)[0] == self._model_id for n in names
            ):
                raise EmbeddingGatewayError(
                    f"ollama model '{self._model_id}' not present on {self._host}"
                )
        except EmbeddingGatewayError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingGatewayError(f"ollama embed ping failed: {exc}") from exc
