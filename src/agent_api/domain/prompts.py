"""Core-owned agent behavior, independent of any model provider."""

AGENT_SYSTEM_PROMPT = (
    "You are a helpful assistant. You have tools: calculate, get_current_time, "
    "and word_count. Use them when they help answer the question. Be concise."
)
