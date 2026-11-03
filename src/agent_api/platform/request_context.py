"""Per-turn request context: the authenticated principal and raw token.

Set by the service around the gateway call for a turn, read by tools
(execute_capability -> HTTP executor) that need the user's identity
downstream. The raw token must NEVER be logged, placed in tool
arguments, conversation history, prompts, or error text — the model
must never see it.

Thread hazard (ADR 0008): Strands runs tools in worker threads, and
contextvars do not cross into pool threads. The service sets this
context in the request thread; the context-propagating tool executor
(strands_base.py) copies the context into each tool invocation. Never
set this variable from inside a worker thread: a value set there
would leak into later turns on the reused thread.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class RequestContext:
    """Who is asking, and the credential proving it (opaque to the core)."""

    principal: str | None = None
    token: str | None = None  # raw bearer token: NEVER log or expose


_current: ContextVar[RequestContext | None] = ContextVar(
    "agent_api_request_context", default=None
)


def get_request_context() -> RequestContext | None:
    """The current turn's request context, or None outside a turn."""
    return _current.get()


@contextmanager
def request_context(
    principal: str | None, token: str | None
) -> Iterator[None]:
    """Set the request context for a turn; always reset it afterwards."""
    reset_token = _current.set(RequestContext(principal=principal, token=token))
    try:
        yield
    finally:
        _current.reset(reset_token)
