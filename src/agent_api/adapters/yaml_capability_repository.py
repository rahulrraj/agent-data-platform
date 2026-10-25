"""YAML-backed CapabilityRepository (<Technology><PortName>, §2).

The registry file is version-controlled configuration: adding a capability is
a repo edit + review + deploy, never a runtime user action.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from agent_api.domain.capability import Capability, ParameterSpec
from agent_api.ports.capability_repository import CapabilityRepositoryError


_META_KEYS = {"id", "description", "kind", "connection", "mode", "parameters"}


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
        for entry in data.get("capabilities", []):
            params = tuple(
                ParameterSpec(
                    name=name,
                    type=spec.get("type", "string"),
                    required=bool(spec.get("required", False)),
                    description=spec.get("description", ""),
                )
                for name, spec in (entry.get("parameters") or {}).items()
            )
            cap = Capability(
                id=entry["id"],
                description=entry["description"],
                kind=entry["kind"],
                connection=entry["connection"],
                mode=entry.get("mode", "read"),
                parameters=params,
                spec={k: v for k, v in entry.items() if k not in _META_KEYS},
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
