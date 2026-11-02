"""HTTP capability executor (<Technology><PortName>, §2).

Connection config (from DATA_CONNECTIONS[<name>]): base_url, api_key
(optional), plus optional extra headers.

Registry `request` spec:
    method: GET | POST
    path: "/orders/{order_number}"   # {placeholders} filled from params
    fields: [status, tracking_number]  # optional allowlist on dict responses
    multi_segment_params: [param_name]  # optional: path params allowed to
        contain "/" (default: a "/" in a path value is refused)

Per-capability trimming settings live on the Capability (top-level
registry keys, validated at load): `list_paths`, `keep`, `max_results`.
See doc/data-capabilities.md. Defaults: cap 50, keep the first rows.
`keep: last` keeps the most recent rows (time-series responses).
The opt-in ceiling is 500; the SQL path's 50-row cap is separate
and unchanged.

GET: params not used in the path become query-string params.
POST: params become the JSON body.

Connection targets are validated at construction (m-4): https-only,
no loopback/private/reserved hosts unless the connection opts in with
allow_private_base_url (local dev). List results are capped (default 50,
opt-in to 500 per capability) with a `truncated` flag, mirroring the
SQL path's cap (m-14); nested lists named by `list_paths` are capped
the same way.

Read-only enforcement: `mode: read` allows GET only. A POST under
`mode: read` is refused here even though the HTTP executor supports POST
in principle — with M-7's SQL guard, that makes retried turns safe by
construction (M-16): nothing mutating is reachable through a read
capability, so re-running a turn cannot double-apply a side effect.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import string
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

# m-8: secret-looking fragments are masked before any error text is
# logged or returned; the model-facing message never carries an upstream
# response body at all (see execute()).
_REDACT_PATTERNS = (
    re.compile(r"(password|passwd|api[_-]?key|secret|token|authorization)(\s*[:=]\s*)\S+", re.I),
    re.compile(r"(Bearer\s+)\S+", re.I),
)


def _redact(text: str) -> str:
    for pattern in _REDACT_PATTERNS:
        text = pattern.sub(r"\1***", text)
    return text

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
        # Message carries the status only (m-8); the body lives on
        # .body for the server-side log in execute(), never for the model.
        super().__init__(f"http {status_code}")
        self.status_code = status_code
        self.body = body


class HttpCapabilityExecutor(CapabilityExecutor):
    """CapabilityExecutor calling REST APIs."""

    def __init__(self, connections: dict, retry_policy: RetryPolicy) -> None:
        self._connections = connections
        self._retry = retry_policy
        for name, config in connections.items():
            if isinstance(config, dict) and "base_url" in config:
                self._validate_base_url(name, config)

    @staticmethod
    def _validate_base_url(name: str, config: dict) -> None:
        """Fail fast on unsafe connection targets (m-4).

        The runtime user cannot set base_url — only env/registry editors
        can — so this is defense in depth for the day that assumption
        weakens, or a base_url is ever derived from a spec. https-only;
        loopback/link-local/private/reserved IPs and localhost names
        are refused unless the connection explicitly opts in with
        allow_private_base_url (local dev only). Hostnames that are not
        literal IPs cannot be checked without DNS resolution, so a
        private hostname is not caught here — that limitation is
        documented, not hidden.
        """
        url = config["base_url"]
        parsed = urllib.parse.urlparse(str(url))
        allow_private = bool(config.get("allow_private_base_url"))
        if parsed.scheme != "https" and not allow_private:
            raise CapabilityExecutorError(
                f"connection '{name}': base_url must use https "
                f"(got scheme {parsed.scheme!r}); set "
                "allow_private_base_url=true only for local dev"
            )
        host = parsed.hostname
        if not host:
            raise CapabilityExecutorError(
                f"connection '{name}': base_url {url!r} has no host"
            )
        if (host == "localhost" or host.endswith((".localhost", ".local"))) and not allow_private:
            raise CapabilityExecutorError(
                f"connection '{name}': base_url host {host!r} is local; "
                "set allow_private_base_url=true only for local dev"
            )
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return  # hostname: not resolvable here without DNS; see docstring
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ) and not allow_private:
            raise CapabilityExecutorError(
                f"connection '{name}': base_url host {host!r} is not a "
                "public address; set allow_private_base_url=true only "
                "for local dev"
            )

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

    @staticmethod
    def _trim_list(items: list, keep: str, max_results: int) -> tuple[list, bool]:
        """Cap one list per the capability's trimming settings.

        Returns (trimmed_items, was_trimmed). `keep: last` keeps the
        tail (most recent rows); anything else keeps the head, as
        before. A list already within the cap is returned unchanged
        (same object) with was_trimmed False.
        """
        if len(items) <= max_results:
            return items, False
        if keep == "last":
            return items[-max_results:], True
        return items[:max_results], True

    @classmethod
    def _trim_at_paths(
        cls, data: dict, list_paths: tuple[str, ...], keep: str, max_results: int
    ) -> bool:
        """Cap each nested list named by a dotted path in `data`.

        A path is a "."-separated chain of dict keys from the response
        root (e.g. "history.prices"). A path that does not resolve —
        a missing key, a non-dict intermediate, or a non-list at the
        end — is left untouched: the registry may name a list the
        upstream only sometimes returns, and that is not an execution
        failure. The parsed response object is mutated in place (it
        is single-use, fresh from response.json()). Returns True if
        any list was trimmed.
        """
        trimmed_any = False
        for path in list_paths:
            segments = path.split(".")
            parent: object = data
            resolved = True
            for segment in segments[:-1]:
                if not isinstance(parent, dict):
                    resolved = False
                    break
                parent = parent.get(segment)
            if not resolved or not isinstance(parent, dict):
                continue
            value = parent.get(segments[-1])
            if not isinstance(value, list):
                continue
            trimmed, was_trimmed = cls._trim_list(value, keep, max_results)
            if was_trimmed:
                trimmed_any = True
                parent[segments[-1]] = trimmed
        return trimmed_any

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
            # m-8: the model/user gets the status only. The upstream
            # body (which can carry server names, fragments, or data)
            # goes to the server log, redacted — never into the turn.
            logger.warning(
                "http capability %s: upstream http %s: %s",
                capability.id, exc.status_code, _redact(exc.body[:200]),
            )
            raise CapabilityExecutorError(
                f"http call failed: upstream returned http {exc.status_code}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "http capability %s failed: %s", capability.id, _redact(str(exc))
            )
            raise CapabilityExecutorError(
                f"http call failed: {_redact(str(exc))}"
            ) from exc

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
        # A malformed template (e.g. an unbalanced "{") is a registry
        # bug, not a missing parameter: report it as such instead of
        # mislabeling it (nit). Literal "{{...}}" escapes parse to no
        # field, so they stay literal — they are not parameters.
        try:
            path_params = {
                field_name
                for _, field_name, _, _ in string.Formatter().parse(path_template)
                if field_name
            }
        except ValueError as exc:
            raise CapabilityExecutorError(
                f"invalid path template {path_template!r}: {exc}"
            ) from exc
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
        except ValueError as exc:
            raise CapabilityExecutorError(
                f"invalid path template {path_template!r}: {exc}"
            ) from exc
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
        # 204 / empty body / non-JSON 200 used to raise from
        # response.json() (nit): an empty upstream answer is data
        # (None), and a non-JSON body is returned as text, not a crash.
        body_text = getattr(response, "text", "") or ""
        if response.status_code == 204 or not body_text.strip():
            return {"data": None}
        try:
            data = response.json()
        except ValueError:
            return {"data": body_text}
        fields = request.get("fields")
        if isinstance(data, list):
            # m-14: lists get the cap (default 50, opt-in to 500 per
            # capability), and `fields` trims list items too
            # (previously it trimmed only dicts, so a list of
            # thousands of objects entered context whole). A
            # top-level list is capped whether or not `list_paths`
            # are set: list_paths only resolve inside dict payloads,
            # so the top-level list is never left uncapped.
            items = data
            if fields:
                items = [
                    {k: item.get(k) for k in fields}
                    if isinstance(item, dict) else item
                    for item in items
                ]
            trimmed, truncated = self._trim_list(
                items, capability.keep, capability.max_results
            )
            return {"data": trimmed, "truncated": truncated}
        if isinstance(data, dict):
            truncated = False
            if capability.list_paths:
                # Nested lists are capped before `fields` filtering,
                # so the allowlist still decides which keys survive.
                truncated = self._trim_at_paths(
                    data, capability.list_paths,
                    capability.keep, capability.max_results,
                )
            if fields:
                data = {k: data.get(k) for k in fields}
            if capability.list_paths:
                # With nested trimming configured the flag is always
                # reported, so the model can tell a capped nested
                # list from a complete one.
                return {"data": data, "truncated": truncated}
            return {"data": data}
        if capability.list_paths:
            return {"data": data, "truncated": False}
        return {"data": data}
