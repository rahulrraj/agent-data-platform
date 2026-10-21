"""Gemini implementation of the ModelGateway port (<Technology><PortName>, §2).

Same stateless-per-call shape as OllamaModelGateway; only the model differs.
"""

from __future__ import annotations

from agent_api.adapters.strands_base import StrandsGatewayBase
from agent_api.platform.retry import RetryPolicy
from agent_api.ports.model_gateway import ModelGatewayError


class GeminiModelGateway(StrandsGatewayBase):
    """ModelGateway backed by Gemini via Google AI Studio."""

    def __init__(
        self,
        api_key: str,
        model_id: str,
        *,
        tools: list,
        system_prompt: str,
        retry_policy: RetryPolicy,
    ) -> None:
        super().__init__(tools=tools, system_prompt=system_prompt, retry_policy=retry_policy)
        self._api_key = api_key
        self._model_id = model_id

    def _provider_name(self) -> str:
        return "gemini"

    def _build_model(self):
        # Local import: only this adapter needs the gemini provider package.
        from strands.models.gemini import GeminiModel

        return GeminiModel(
            model_id=self._model_id,
            client_args={"api_key": self._api_key},
        )

    def _is_transient(self, exc: Exception) -> bool:
        try:
            from google.api_core import exceptions as google_exc
        except ImportError:
            return False
        return isinstance(
            exc,
            (
                google_exc.ServiceUnavailable,
                google_exc.DeadlineExceeded,
                google_exc.ResourceExhausted,
                google_exc.Internal,
                google_exc.BadGateway,
            ),
        )

    def ping(self) -> None:
        """Cheap readiness check: key configured.

        A live inference ping would burn API quota on every probe, so readiness
        here means 'configured'. The first real call proves the rest.
        """
        if not self._api_key:
            raise ModelGatewayError("gemini not reachable: GEMINI_API_KEY is not set")
