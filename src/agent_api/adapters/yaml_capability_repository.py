"""YAML-backed CapabilityRepository (<Technology><PortName>, §2).

The registry file is version-controlled configuration: adding a capability is
a repo edit + review + deploy, never a runtime user action.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from agent_api.domain.capability import (
    DEFAULT_MAX_RESULTS,
    MAX_MAX_RESULTS,
    Capability,
    ParameterSpec,
)
from agent_api.ports.capability_repository import CapabilityRepositoryError


_META_KEYS = {
    "id", "description", "kind", "connection", "mode", "parameters",
    # Result-trimming settings: top-level registry keys, carried on the
    # Capability itself (not in spec) — see doc/data-capabilities.md.
    "list_paths", "keep", "max_results",
}

# m-18 / m-15: load-time allowlists. A typo'd kind, mode, or parameter
# type used to deploy green and fail only on first execution; now the
# registry fails fast at startup with the entry named.
_KINDS = {"snowflake", "sqlserver", "http"}
_MODES = {"read"}
_PARAM_TYPES = {"string", "integer", "number", "boolean"}


class YamlCapabilityRepository:
    """CapabilityRepository reading registry/capabilities.yaml."""

    def __init__(self, path: str) -> None:
        self._path = path
        try:
            self._capabilities = self._load(path)
        except CapabilityRepositoryError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CapabilityRepositoryError(f"registry load failed: {exc}") from exc

    @staticmethod
    def _load(path: str) -> dict[str, Capability]:
        file = Path(path)
        if not file.is_file():
            raise FileNotFoundError(
                f"capability registry not found: {path} "
                "(set CAPABILITIES_PATH or create registry/capabilities.yaml)"
            )
        with file.open() as fh:
            data = yaml.safe_load(fh) or {}
        capabilities: dict[str, Capability] = {}
        for position, entry in enumerate(data.get("capabilities", [])):
            if not isinstance(entry, dict):
                raise ValueError(
                    f"registry entry {position} is not a mapping: {entry!r}"
                )
            for field in ("id", "description", "kind", "connection"):
                if not entry.get(field):
                    raise ValueError(
                        f"registry entry {position}: missing required "
                        f"field {field!r}"
                    )
            cap_id = entry["id"]
            if entry["kind"] not in _KINDS:
                raise ValueError(
                    f"capability {cap_id!r}: unknown kind "
                    f"{entry['kind']!r} (expected one of {sorted(_KINDS)})"
                )
            mode = entry.get("mode", "read")
            if mode not in _MODES:
                raise ValueError(
                    f"capability {cap_id!r}: unknown mode {mode!r} "
                    f"(expected one of {sorted(_MODES)})"
                )
            raw_params = entry.get("parameters") or {}
            if not isinstance(raw_params, dict):
                raise ValueError(
                    f"capability {cap_id!r}: 'parameters' must be a mapping"
                )
            params_list = []
            for name, spec in raw_params.items():
                if not isinstance(spec, dict):
                    raise ValueError(
                        f"capability {cap_id!r}: parameter {name!r} must "
                        "be a mapping"
                    )
                ptype = spec.get("type", "string")
                if ptype not in _PARAM_TYPES:
                    raise ValueError(
                        f"capability {cap_id!r}: parameter {name!r} has "
                        f"unknown type {ptype!r} "
                        f"(expected one of {sorted(_PARAM_TYPES)})"
                    )
                params_list.append(
                    ParameterSpec(
                        name=name,
                        type=ptype,
                        required=bool(spec.get("required", False)),
                        description=spec.get("description", ""),
                    )
                )
            spec = {k: v for k, v in entry.items() if k not in _META_KEYS}
            # Result-trimming settings (HTTP executors only; validated
            # here at load for every kind so a typo fails fast at
            # startup, like the kind/mode/type allowlists above).
            # The Capability constructor re-checks the same invariants;
            # these checks name the registry entry, as the others do.
            list_paths = entry.get("list_paths", [])
            if isinstance(list_paths, str) or not isinstance(list_paths, list):
                raise ValueError(
                    f"capability {cap_id!r}: 'list_paths' must be a list "
                    f"of non-empty strings (got {list_paths!r})"
                )
            for path in list_paths:
                if not isinstance(path, str) or not path:
                    raise ValueError(
                        f"capability {cap_id!r}: 'list_paths' must be a "
                        f"list of non-empty strings (got {path!r})"
                    )
            keep = entry.get("keep", "first")
            if keep not in ("first", "last"):
                raise ValueError(
                    f"capability {cap_id!r}: 'keep' must be 'first' or "
                    f"'last' (got {keep!r})"
                )
            max_results = entry.get("max_results", DEFAULT_MAX_RESULTS)
            if isinstance(max_results, bool) or not isinstance(max_results, int):
                raise ValueError(
                    f"capability {cap_id!r}: 'max_results' must be an "
                    f"integer (got {max_results!r})"
                )
            if not 1 <= max_results <= MAX_MAX_RESULTS:
                raise ValueError(
                    f"capability {cap_id!r}: 'max_results' must be between "
                    f"1 and {MAX_MAX_RESULTS} (got {max_results})"
                )
            if entry["kind"] in ("snowflake", "sqlserver"):
                if not spec.get("query"):
                    raise ValueError(
                        f"capability {cap_id!r}: kind "
                        f"{entry['kind']!r} requires a 'query'"
                    )
            else:  # http
                request = spec.get("request")
                if not isinstance(request, dict) or not request.get("path"):
                    raise ValueError(
                        f"capability {cap_id!r}: kind 'http' requires "
                        "'request.path'"
                    )
                method = str(request.get("method", "GET")).upper()
                if method not in ("GET", "POST"):
                    raise ValueError(
                        f"capability {cap_id!r}: unsupported http method "
                        f"{method!r}"
                    )
            cap = Capability(
                id=cap_id,
                description=entry["description"],
                kind=entry["kind"],
                connection=entry["connection"],
                mode=mode,
                parameters=tuple(params_list),
                spec=spec,
                list_paths=tuple(list_paths),
                keep=keep,
                max_results=max_results,
            )
            if cap.id in capabilities:
                raise ValueError(f"duplicate capability id: {cap.id}")
            capabilities[cap.id] = cap
        return capabilities

    def list_all(self) -> list[Capability]:
        return list(self._capabilities.values())

    def get(self, capability_id: str) -> Capability | None:
        return self._capabilities.get(capability_id)

    def ping(self) -> None:
        try:
            Path(self._path).stat()  # raises if unreadable
        except CapabilityRepositoryError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CapabilityRepositoryError(f"registry ping failed: {exc}") from exc
