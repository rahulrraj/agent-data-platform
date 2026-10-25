"""HTTP capability executor (<Technology><PortName>, §2).

Connection config (from DATA_CONNECTIONS[<name>]): base_url, api_key
(optional), plus optional extra headers.

Registry `request` spec:
    method: GET | POST
    path: "/orders/{order_number}"   # {placeholders} filled from params
    fields: [status, tracking_number]  # optional allowlist on dict responses
    multi_segment_params: [param_name]  # optional: path params allowed to
        contain "/" (default: a "/" in a path value is refused)

GET: params not used in the path become query-string params.
POST: params become the JSON body.

Read-only enforcement: `mode: read` allows GET only. A POST under
`mode: read` is refused here even though the HTTP executor supports POST
in principle — with M-7's SQL guard, that makes retried turns safe by
construction (M-16): nothing mutating is reachable through a read
capability, so re-running a turn cannot double-apply a side effect.
"""

from __future__ import annotations

import string
import urllib.parse

import httpx

from agent_api.domain.capability import Capability
from agent_api.platform.retry import RetryPolicy, call_with_retry
from agent_api.ports.capability_executor import (
    CapabilityExecutor,
    CapabilityExecutorError,
)


class HttpStatusError(Exception):
    """Internal: an upstream HTTP failure carrying its status code.

    Raised by _execute_once (inside the retried call) so _is_transient
    can classify 429/5xx and retry them. Translated to
    CapabilityExecutorError by execute() once retries are exhausted —
    the port error is what callers ever see.
    """

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"http {status_code}: {body[:200]}")
        self.status_code = status_code
        self.body = body


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
        request = capability.spec.get("request") or {}
        method = request.get("method", "GET").upper()
        if method != "GET":
            # mode == "read" (checked above) + a non-GET method would
            # reach a potentially mutating endpoint: refuse, don't execute.
            raise CapabilityExecutorError(
                f"refusing {method} under mode=read for capability "
                f"'{capability.id}' (read capabilities are GET-only)"
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
        except HttpStatusError as exc:
            raise CapabilityExecutorError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise CapabilityExecutorError(f"http call failed: {exc}") from exc

    def _execute_once(
        self, capability: Capability, params: dict, config: dict
    ) -> dict:
        request = capability.spec.get("request") or {}
        method = request.get("method", "GET").upper()
        path_template = request.get("path", "")
        # Path values are percent-encoded before interpolation so a value
        # can never smuggle a new path segment or query string into the
        # URL (e.g. "../admin" or "x?admin=true"). A "/" in a value is
        # refused outright unless the capability explicitly opts that
        # parameter into multi-segment paths.
        path_params = {
            field_name
            for _, field_name, _, _ in string.Formatter().parse(path_template)
            if field_name
        }
        multi_segment = set(request.get("multi_segment_params") or [])
        encoded: dict[str, str] = {}
        for name in path_params:
            if name not in params:
                raise CapabilityExecutorError(f"missing path parameter: {name!r}")
            value = str(params[name])
            if "/" in value and name not in multi_segment:
                raise CapabilityExecutorError(
                    f"path parameter '{name}' must not contain '/'"
                )
            # Opted-in params keep "/" as a segment separator; everything
            # else is fully encoded (including "/").
            encoded[name] = urllib.parse.quote(
                value, safe="/" if name in multi_segment else ""
            )
        try:
            path = path_template.format(**encoded)
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
                    if f"{{{k}}}" not in path_template
                }
                response = client.get(url, headers=headers, params=query)
            elif method == "POST":
                response = client.post(url, headers=headers, json=params)
            else:
                raise CapabilityExecutorError(f"unsupported method: {method}")
            if response.status_code >= 400:
                # Status-carrying error: _is_transient retries 429/5xx.
                raise HttpStatusError(response.status_code, response.text)
        data = response.json()
        fields = request.get("fields")
        if fields and isinstance(data, dict):
            data = {k: data.get(k) for k in fields}
        return {"data": data}
