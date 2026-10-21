"""Agent factory: wires the model, system prompt, and tools into a Strands agent."""

from agent_api import config
from agent_api.tools import TOOLS
from strands import Agent

SYSTEM_PROMPT = (
    "You are a helpful assistant. You have tools: calculate, get_current_time, "
    "and word_count. Use them when they help answer the question. Be concise."
)


def create_agent() -> Agent:
    """Builds a Strands agent on the configured model provider."""
    if config.PROVIDER == "gemini":
        from strands.models.gemini import GeminiModel

        if not config.GEMINI_API_KEY:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Get a free key at "
                "https://aistudio.google.com/apikey and export GEMINI_API_KEY=<key>."
            )
        model = GeminiModel(
            model_id=config.GEMINI_MODEL,
            client_args={"api_key": config.GEMINI_API_KEY},
        )
    elif config.PROVIDER == "ollama":
        from strands.models.ollama import OllamaModel

        model = OllamaModel(
            host=config.OLLAMA_HOST,
            model_id=config.OLLAMA_MODEL,
            temperature=0,
            max_tokens=512,
        )
    else:
        raise RuntimeError(f"Unknown AGENT_MODEL_PROVIDER: {config.PROVIDER!r} (use 'ollama' or 'gemini')")
    return Agent(model=model, system_prompt=SYSTEM_PROMPT, tools=TOOLS)
