"""LangGraph + Ollama implementation of the ModelGateway port.

Same stateless-per-call shape as the Strands side; only the agent runtime
differs. Chosen via AGENT_FRAMEWORK=langgraph (AGENT_MODEL_PROVIDER=ollama).
"""

from __future__ import annotations

import os

for _key in [k for k in os.environ if "proxy" in k.lower()]:
    # Sandbox quirk: langchain_ollama imports the `ollama` package, which
    # creates an HTTP client at import time — the sandbox's proxy values
    # break it. Localhost needs no proxy. Must run before that import.
    del os.environ[_key]

import httpx

from agent_api.adapters.langgraph_base import LangGraphGatewayBase
from agent_api.platform.retry import RetryPolicy
from agent_api.ports.model_gateway import ModelGatewayError


class LangGraphOllamaGateway(LangGraphGatewayBase):
    """ModelGateway: LangGraph ReAct agent over a local Ollama model."""

    def __init__(
        self,
        host: str,
        model_id: str,
        *,
        tools: list,
        system_prompt: str,
        retry_policy: RetryPolicy,
        timeout: float = 120,
    ) -> None:
        super().__init__(tools=tools, system_prompt=system_prompt, retry_policy=retry_policy)
        self._host = host
        self._model_id = model_id
        self._timeout = timeout

    def _provider_name(self) -> str:
        return "langgraph/ollama"

    def _build_chat_model(self):
        # Local import: only this adapter needs langchain_ollama.
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=self._model_id,
            base_url=self._host,
            temperature=0,
            timeout=self._timeout,
        )

    def _is_transient(self, exc: Exception) -> bool:
        if isinstance(
            exc,
            (
                httpx.ConnectError,
                httpx.TimeoutException,
                httpx.RemoteProtocolError,
                httpx.PoolTimeout,
            ),
        ):
            return True
        status = getattr(exc, "status_code", None)
        return status == 429 or (isinstance(status, int) and 500 <= status < 600)

    def ping(self) -> None:
        """Cheap reachability check: the server's tag list, no inference."""
        try:
            # trust_env=False: the sandbox's no_proxy values break httpx parsing.
            with httpx.Client(trust_env=False, timeout=5) as client:
                response = client.get(f"{self._host}/api/tags")
            if response.status_code != 200:
                raise ModelGatewayError(f"ollama not reachable: HTTP {response.status_code}")
        except ModelGatewayError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ModelGatewayError(f"ollama not reachable: {exc}") from exc
