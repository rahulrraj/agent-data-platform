"""Returns the current date and time."""

from datetime import datetime

from strands import tool


@tool
def get_current_time() -> str:
    """Returns the current date and time."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
