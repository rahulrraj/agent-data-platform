"""Core-owned agent behavior, independent of any model provider.

The system prompt is built from the discovered tool names so adding a plugin
never requires editing the prompt by hand.
"""


def build_system_prompt(tool_names: list[str]) -> str:
    """System prompt naming the agent's available tools."""
    names = ", ".join(tool_names)
    return (
        "You are a helpful assistant. "
        f"You have tools: {names}. "
        "Use them when they help answer the question. Be concise."
    )
