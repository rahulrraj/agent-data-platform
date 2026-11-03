"""Core domain types: the technology-independent conversation model.

Domain depends on nothing but itself (§3: domain may only call domain).
"""

from __future__ import annotations

from dataclasses import dataclass, field

USER = "user"
ASSISTANT = "assistant"


@dataclass(frozen=True)
class ToolResult:
    """One tool call's outcome inside a turn: which tool, what it returned.

    Stored verbatim (execute_capability results) so the next turn can see
    prior tool outputs instead of only the assistant's summary of them.
    """

    tool: str
    result: str


@dataclass(frozen=True)
class Turn:
    """One turn in a conversation: who said it, what was said, which tools ran."""

    role: str
    content: str
    tool_uses: tuple[str, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()


@dataclass
class Conversation:
    """The application's own record of a conversation (history the core owns)."""

    id: str
    turns: list[Turn] = field(default_factory=list)
    # ADR 0008: the principal (`sub`) the session is bound to. None
    # means unowned — dev mode (auth off), or a session created before
    # ownership existed; the first authenticated writer claims it.
    owner: str | None = None

    def add_user(self, text: str) -> None:
        self.turns.append(Turn(role=USER, content=text))

    def add_assistant(
        self,
        text: str,
        tool_uses: tuple[str, ...] | list[str] = (),
        tool_results: tuple[ToolResult, ...] | list[ToolResult] = (),
    ) -> None:
        self.turns.append(
            Turn(
                role=ASSISTANT,
                content=text,
                tool_uses=tuple(tool_uses),
                tool_results=tuple(tool_results),
            )
        )
