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
            # m-10: bound the outbound call like Ollama's 120s. The
            # google-genai HttpOptions timeout is in milliseconds.
            client_args={
                "api_key": self._api_key,
                "http_options": {"timeout": 120_000},
            },
        )

    def _is_transient(self, exc: Exception) -> bool:
        # Match what the SDKs actually raise (verified against the
        # installed packages): Strands converts 429/503 into
        # ModelThrottledException, and the google-genai client raises
        # APIError subclasses carrying a numeric `code`. The old check
        # against google.api_core exceptions never matched either — and
        # google.api_core is not even installed — so 429/503 were never
        # retried.
        try:
            from strands.types.exceptions import ModelThrottledException

            if isinstance(exc, ModelThrottledException):
                return True
        except ImportError:
            pass
        try:
            from google.genai import errors as genai_errors
        except ImportError:
            return False
        if isinstance(exc, genai_errors.APIError):
            code = getattr(exc, "code", None)
            return code == 429 or (isinstance(code, int) and 500 <= code < 600)
        return False

    def ping(self) -> None:
        """Cheap readiness check: key configured.

        A live inference ping would burn API quota on every probe, so readiness
        here means 'configured'. The first real call proves the rest.
        """
        if not self._api_key:
            raise ModelGatewayError("gemini not reachable: GEMINI_API_KEY is not set")
