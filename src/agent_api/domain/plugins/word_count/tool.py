"""Counts the words in a piece of text."""

from strands import tool


@tool
def word_count(text: str) -> str:
    """Counts the words in the given text."""
    return str(len(text.split()))
