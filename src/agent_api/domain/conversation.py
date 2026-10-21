"""Core domain types: the technology-independent conversation model.

Domain depends on nothing but itself (§3: domain may only call domain).
"""

from __future__ import annotations

from dataclasses import dataclass, field

USER = "user"
ASSISTANT = "assistant"


@dataclass(frozen=True)
class Turn:
    """One turn in a conversation: who said it, what was said, which tools ran."""

    role: str
    content: str
    tool_uses: tuple[str, ...] = ()


@dataclass
class Conversation:
    """The application's own record of a conversation (history the core owns)."""

    id: str
    turns: list[Turn] = field(default_factory=list)

    def add_user(self, text: str) -> None:
        self.turns.append(Turn(role=USER, content=text))

    def add_assistant(self, text: str, tool_uses: tuple[str, ...] | list[str] = ()) -> None:
        self.turns.append(Turn(role=ASSISTANT, content=text, tool_uses=tuple(tool_uses)))
