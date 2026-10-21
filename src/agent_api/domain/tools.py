"""Agent capabilities as pure functions.

These tools perform no I/O, so they live in the domain (technology-independent).
Add new @tool functions here and register them in TOOLS. The gateway adapters
hand them to the model runtime.
"""

import math
from datetime import datetime

from strands import tool


@tool
def calculate(expression: str) -> str:
    """Evaluates a math expression. Example: calculate(expression="6 * 7")."""
    allowed = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
    try:
        return str(eval(expression, {"__builtins__": {}}, allowed))  # noqa: S307
    except Exception as exc:  # noqa: BLE001
        return f"Error: {exc}"


@tool
def get_current_time() -> str:
    """Returns the current date and time."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@tool
def word_count(text: str) -> str:
    """Counts the words in the given text."""
    return str(len(text.split()))


TOOLS = [calculate, get_current_time, word_count]
