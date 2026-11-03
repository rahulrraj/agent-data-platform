"""Outbound port: the core's contract for a model with tool support.

The model is a capability owned by another system, so this is an integration
gateway port (§4). Port-specific errors live beside the port (§5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from agent_api.domain.conversation import Conversation, Turn


class ModelGatewayError(Exception):
    """A model call failed. Adapters translate provider errors into this."""


@dataclass(frozen=True)
class StreamChunk:
    """One core-owned streaming event. kind is 'text' or 'tool'."""

    kind: str
    payload: str


class ModelGateway(Protocol):
    """Contract every model adapter must honor. Technology-independent types only."""

    def generate(self, conversation: Conversation) -> Turn:
        """Run one agentic turn (model + tool loop); return the assistant's turn."""
        ...

    def stream(self, conversation: Conversation) -> AsyncIterator[StreamChunk]:
        """Same as generate(), but yields text/tool chunks as they arrive."""
        ...

    def ping(self) -> None:
        """Cheap reachability check; raise ModelGatewayError when the provider
        is down. Must not run inference (probes are frequent)."""
        ...


class ModelGatewayResolver(Protocol):
    """Resolves a per-request model key to a ModelGateway (ADR 0007).

    Implemented by the composition root's factory; the service only
    knows this contract, never the framework × provider matrix. Keys
    are opaque allowlist keys (see Settings.allowed_models); ``None``
    or ``"default"`` always means the configured provider + model.
    Unknown keys raise KeyError — the service translates that into an
    InvalidMessageError (400) listing the available keys.
    """

    @property
    def available_keys(self) -> tuple[str, ...]:
        """Caller-facing keys, with ``"default"`` first."""
        ...

    def gateway_for(self, key: str | None) -> ModelGateway:
        """Return the gateway for `key`, building and caching it on first
        use. Construction failures surface as ModelGatewayError (a
        per-request 502), never as a startup error."""
        ...

    def describe(self, key: str | None) -> tuple[str, str] | None:
        """The (provider, model id) behind `key`, for span attribution.

        Returns None when the key is unknown (the caller already
        rejected it) — attribution is best-effort and must never fail
        a turn. Added for ADR 0009: the turn span records which
        provider/model served it without the service knowing the
        matrix.
        """
        ...
