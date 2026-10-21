"""CapabilityExecutor: run one capability against its external system.

One adapter per `kind` (snowflake, sqlserver, http). The adapter owns the
SDK/driver, the connection handling, and the transient-error classification.
The tool layer never touches these.
"""

from __future__ import annotations

from typing import Protocol

from agent_api.domain.capability import Capability


class CapabilityExecutorError(Exception):
    """A capability could not be executed (bad config, bad params, downstream failure)."""


class CapabilityExecutor(Protocol):
    """Outbound port for executing data capabilities of one kind."""

    @property
    def kind(self) -> str:
        """The capability kind this executor handles, e.g. "snowflake"."""
        ...

    def execute(self, capability: Capability, params: dict) -> dict:
        """Run the capability with validated params; return JSON-serializable result."""
        ...
