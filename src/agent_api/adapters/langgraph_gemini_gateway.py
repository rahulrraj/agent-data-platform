"""LangGraph + Gemini implementation of the ModelGateway port.

Same stateless-per-call shape as the Strands side; only the agent runtime
differs. Chosen via AGENT_FRAMEWORK=langgraph (AGENT_MODEL_PROVIDER=gemini).
"""

from __future__ import annotations

from agent_api.adapters.langgraph_base import LangGraphGatewayBase
from agent_api.platform.retry import RetryPolicy
from agent_api.ports.model_gateway import ModelGatewayError


class LangGraphGeminiGateway(LangGraphGatewayBase):
    """ModelGateway: LangGraph ReAct agent over Gemini."""

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
        return "langgraph/gemini"

    def _build_chat_model(self):
        # Local import: only this adapter needs langchain_google_genai.
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=self._model_id,
            api_key=self._api_key,
            temperature=0,
        )

    def _is_transient(self, exc: Exception) -> bool:
        # Match what langchain_google_genai actually raises (verified
        # against the installed package): 429 -> GoogleRateLimitError, 5xx
        # -> GoogleAPIError (a genai ServerError). langchain_core's
        # ModelRateLimitError covers the same 429 case through the base
        # class, and a raw genai APIError is classified by its code.
        # The old google.api_core check never matched either type — and
        # google.api_core is not installed — so 429/503 were never retried.
        try:
            from langchain_google_genai.chat_models import (
                GoogleAPIError,
                GoogleRateLimitError,
            )

            if isinstance(exc, (GoogleRateLimitError, GoogleAPIError)):
                return True
        except ImportError:
            pass
        try:
            from langchain_core.exceptions import ModelRateLimitError

            if isinstance(exc, ModelRateLimitError):
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
