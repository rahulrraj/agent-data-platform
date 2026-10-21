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
