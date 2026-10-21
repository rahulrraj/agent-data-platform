"""Evaluates a math expression."""

import math

from strands import tool


@tool
def calculate(expression: str) -> str:
    """Evaluates a math expression. Example: calculate(expression="6 * 7")."""
    allowed = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
    try:
        return str(eval(expression, {"__builtins__": {}}, allowed))  # noqa: S307
    except Exception as exc:  # noqa: BLE001
        return f"Error: {exc}"
