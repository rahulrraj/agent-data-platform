"""HTTP capability executor (<Technology><PortName>, §2).

Connection config (from DATA_CONNECTIONS[<name>]): base_url, api_key
(optional), plus optional extra headers.

Registry `request` spec:
    method: GET | POST
    path: "/orders/{order_number}"   # {placeholders} filled from params
    fields: [status, tracking_number]  # optional allowlist on dict responses

GET: params not used in the path become query-string params.
POST: params become the JSON body.
"""

from __future__ import annotations

import httpx

from agent_api.domain.capability import Capability
from agent_api.platform.retry import RetryPolicy, call_with_retry
from agent_api.ports.capability_executor import (
    CapabilityExecutor,
    CapabilityExecutorError,
)


class HttpCapabilityExecutor(CapabilityExecutor):
    """CapabilityExecutor calling REST APIs."""

    def __init__(self, connections: dict, retry_policy: RetryPolicy) -> None:
        self._connections = connections
        self._retry = retry_policy

    @property
    def kind(self) -> str:
        return "http"

    def _is_transient(self, exc: Exception) -> bool:
        if isinstance(
            exc,
            (
                httpx.ConnectError,
                httpx.TimeoutException,
                httpx.RemoteProtocolError,
            ),
        ):
            return True
        status = getattr(exc, "status_code", None)
        return status == 429 or (isinstance(status, int) and 500 <= status < 600)

    def execute(self, capability: Capability, params: dict) -> dict:
        if capability.mode != "read":
            raise CapabilityExecutorError(
                f"refusing non-read capability '{capability.id}' (mode={capability.mode})"
            )
        try:
            config = self._connections[capability.connection]
        except KeyError:
            raise CapabilityExecutorError(
                f"connection '{capability.connection}' is not configured "
                "(DATA_CONNECTIONS)"
            ) from None
        try:
            return call_with_retry(
                lambda: self._execute_once(capability, params, config),
                policy=self._retry,
                is_transient=self._is_transient,
            )
        except CapabilityExecutorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CapabilityExecutorError(f"http call failed: {exc}") from exc

    def _execute_once(
        self, capability: Capability, params: dict, config: dict
    ) -> dict:
        request = capability.spec.get("request") or {}
        method = request.get("method", "GET").upper()
        try:
            path = request["path"].format(**params)
        except KeyError as exc:
            raise CapabilityExecutorError(f"missing path parameter: {exc}") from exc
        url = config["base_url"].rstrip("/") + path
        headers = dict(config.get("headers") or {})
        if config.get("api_key"):
            headers.setdefault("Authorization", f"Bearer {config['api_key']}")

        # trust_env=False: the sandbox's no_proxy values break httpx parsing.
        with httpx.Client(trust_env=False, timeout=30) as client:
            if method == "GET":
                query = {
                    k: v
                    for k, v in params.items()
                    if f"{{{k}}}" not in request.get("path", "")
                }
                response = client.get(url, headers=headers, params=query)
            elif method == "POST":
                response = client.post(url, headers=headers, json=params)
            else:
                raise CapabilityExecutorError(f"unsupported method: {method}")
            if response.status_code >= 400:
                raise CapabilityExecutorError(
                    f"http {response.status_code}: {response.text[:200]}"
                )
        data = response.json()
        fields = request.get("fields")
        if fields and isinstance(data, dict):
            data = {k: data.get(k) for k in fields}
        return {"data": data}
