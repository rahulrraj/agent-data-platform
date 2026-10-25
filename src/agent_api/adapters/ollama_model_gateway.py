"""Ollama implementation of the ModelGateway port (<Technology><PortName>, §2).

Stateless per call: it seeds a Strands agent from the conversation history and
runs one turn. History itself stays owned by the ConversationRepository, so the
adapter never accumulates hidden state.

Proxy handling lives in the composition root (see ADR 0005): this module
no longer touches os.environ at import time. The per-call Ollama client
itself is built with trust_env=False, so model calls never consult proxy
env vars either.
"""

from __future__ import annotations

import httpx

from agent_api.adapters.strands_base import StrandsGatewayBase
from agent_api.platform.retry import RetryPolicy
from agent_api.ports.model_gateway import ModelGatewayError


class OllamaModelGateway(StrandsGatewayBase):
    """ModelGateway backed by a local Ollama server."""

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
        return "ollama"

    def _build_model(self):
        # Local import: only this adapter needs the ollama provider package.
        from strands.models.ollama import OllamaModel

        return OllamaModel(
            host=self._host,
            model_id=self._model_id,
            temperature=0,  # small models go off the rails otherwise
            max_tokens=512,
            # trust_env=False: proxy env vars must not affect localhost
            # model calls (see module docstring / ADR 0005).
            ollama_client_args={"timeout": self._timeout, "trust_env": False},
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
        """Cheap reachability check: the server's tag list, no inference —
        and the configured model must actually be present in that list.
        A reachable server without the model would fail every turn.
        """
        try:
            # trust_env=False: the sandbox's no_proxy values break httpx parsing.
            with httpx.Client(trust_env=False, timeout=5) as client:
                response = client.get(f"{self._host}/api/tags")
            if response.status_code != 200:
                raise ModelGatewayError(f"ollama not reachable: HTTP {response.status_code}")
            names = [m.get("name", "") for m in response.json().get("models", [])]
            if self._model_id not in names and not any(
                n.rsplit(":", 1)[0] == self._model_id for n in names
            ):
                raise ModelGatewayError(
                    f"ollama model '{self._model_id}' not present on {self._host} "
                    f"(available: {sorted(names)})"
                )
        except ModelGatewayError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ModelGatewayError(f"ollama not reachable: {exc}") from exc
