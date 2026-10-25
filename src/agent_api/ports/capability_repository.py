"""CapabilityRepository: app-owned data (the registry of data capabilities).

The registry itself is ours — which capabilities exist, what they do, what
parameters they take. How they *execute* is a gateway concern.
"""

from __future__ import annotations

from typing import Protocol

from agent_api.domain.capability import Capability


class CapabilityRepositoryError(Exception):
    """A capability registry operation failed (load/ping)."""


class CapabilityRepository(Protocol):
    """Outbound port for reading the capability registry."""

    def list_all(self) -> list[Capability]:
        """Every registered capability."""
        ...

    def get(self, capability_id: str) -> Capability | None:
        """One capability by id, or None."""
        ...

    def ping(self) -> None:
        """Cheap check that the registry is readable; raise when not."""
        ...
