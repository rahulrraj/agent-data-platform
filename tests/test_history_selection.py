"""Tests for relevance-based history selection (history_selection.py).

The fake embedding gateway mirrors the shape of the existing test fakes
(e.g. tests/tools/test_data_tools.py FakeEmbeddings: model_name,
similarity_threshold, embed, ping) — keyword text -> one-hot topic
vector, so similarity is deterministic: 1.0 for a shared topic, 0.0
otherwise.
"""

from __future__ import annotations

import logging

import pytest

from agent_api.domain.conversation import ASSISTANT, USER, ToolResult, Turn
from agent_api.services.history_selection import (
    ABSOLUTE_MAX_HISTORY_TURNS,
    HistorySelection,
    select_history_turns,
)


class TopicEmbeddingGateway:
    """Deterministic fake: each topic word maps to one vector dimension."""

    model_name = "fake-topic-model"
    similarity_threshold = 0.60

    TOPICS = ("france", "invoice", "weather")

    def embed(self, texts):
        vectors = []
        for text in texts:
            lowered = text.lower()
            vectors.append(
                [1.0 if topic in lowered else 0.0 for topic in self.TOPICS]
            )
        return vectors

    def ping(self):
        return None


class RaisingEmbeddingGateway(TopicEmbeddingGateway):
    def embed(self, texts):
        raise RuntimeError("provider down")


class ShortEmbeddingGateway(TopicEmbeddingGateway):
    def embed(self, texts):
        return [[1.0, 0.0, 0.0]]  # one vector, regardless of input count


class CountingEmbeddingGateway(TopicEmbeddingGateway):
    """Counts embed calls; delegates the vectors to the topic fake."""

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return super().embed(texts)


def _exchange(user_text, assistant_text, tool_results=()):
    turns = [Turn(role=USER, content=user_text)]
    turns.append(
        Turn(
            role=ASSISTANT,
            content=assistant_text,
            tool_results=tuple(tool_results),
        )
    )
    return turns


def _history():
    """Old Paris exchange, then newer invoice + weather exchanges."""
    turns: list[Turn] = []
    turns += _exchange(
        "What is the capital of France?",
        "Paris is the capital of France.",
    )
    turns += _exchange(
        "Show me invoice 1042",
        "Invoice 1042 totals $1,200.",
    )
    turns += _exchange(
        "What is the weather in Boston?",
        "Boston is 72F and sunny.",
    )
    return turns


def test_old_relevant_turn_included_newer_irrelevant_turn_dropped():
    """(a) Relevance beats recency: the old Paris exchange is kept,
    the newer invoice/weather exchanges are dropped — except the
    immediately preceding (weather) exchange, kept for continuity."""
    gateway = TopicEmbeddingGateway()
    selection = select_history_turns(
        _history(), "What is the capital of France again?", gateway
    )
    assert not selection.fallback_used
    contents = [t.content for t in selection.turns]
    assert "What is the capital of France?" in contents
    assert "Paris is the capital of France." in contents
    # Invoice exchange: neither relevant nor most recent -> dropped.
    assert "Show me invoice 1042" not in contents
    assert "Invoice 1042 totals $1,200." not in contents
    # Weather exchange: irrelevant, but it is the immediately
    # preceding exchange, so continuity keeps it.
    assert "What is the weather in Boston?" in contents


def test_continuity_pair_kept_when_nothing_is_relevant():
    """(b) With no relevant past, only the immediately preceding
    exchange is sent — the model still sees what 'it' refers to."""
    gateway = TopicEmbeddingGateway()
    selection = select_history_turns(
        _history(), "Tell me a joke about penguins", gateway
    )
    assert not selection.fallback_used
    contents = [t.content for t in selection.turns]
    assert contents == [
        "What is the weather in Boston?",
        "Boston is 72F and sunny.",
    ]


def test_forty_turn_ceiling_respected():
    """(c) A 100-turn history still yields a small window, far under 40."""
    gateway = TopicEmbeddingGateway()
    turns: list[Turn] = []
    for i in range(50):
        turns += _exchange(f"invoice question {i}", f"invoice answer {i}")
    turns += _exchange(
        "What is the capital of France?", "Paris is the capital of France."
    )
    selection = select_history_turns(
        turns, "What is the capital of France again?", gateway
    )
    assert len(selection.turns) <= ABSOLUTE_MAX_HISTORY_TURNS
    assert len(selection.turns) <= 6  # at most 3 exchanges
    assert "What is the capital of France?" in [t.content for t in selection.turns]


def test_recency_fallback_without_gateway(caplog):
    """(d) No embedding gateway -> recency fallback, flagged + logged."""
    with caplog.at_level(logging.INFO, logger="agent_api.services.history_selection"):
        selection = select_history_turns(_history(), "anything", None)
    assert selection.fallback_used
    assert "no embedding gateway" in selection.reason
    assert "fell back to recency" in caplog.text
    # Last 2 exchanges (invoice + weather), chronological.
    assert [t.content for t in selection.turns] == [
        "Show me invoice 1042",
        "Invoice 1042 totals $1,200.",
        "What is the weather in Boston?",
        "Boston is 72F and sunny.",
    ]


def test_recency_fallback_when_embedding_raises(caplog):
    """(d) An embedding failure degrades to recency, never raises."""
    gateway = RaisingEmbeddingGateway()
    with caplog.at_level(logging.INFO, logger="agent_api.services.history_selection"):
        selection = select_history_turns(_history(), "anything", gateway)
    assert selection.fallback_used
    assert "embedding failed" in selection.reason
    assert "fell back to recency" in caplog.text
    assert [t.content for t in selection.turns][-1] == "Boston is 72F and sunny."


def test_recency_fallback_when_embedding_returns_short_result():
    """(d) A malformed embedding result (wrong vector count) also
    falls back to recency instead of mis-ranking."""
    selection = select_history_turns(_history(), "anything", ShortEmbeddingGateway())
    assert selection.fallback_used
    assert "vectors for" in selection.reason


def test_returned_turns_in_chronological_order():
    """(e) Selected turns come back in stored order, not ranked order:
    the old relevant Paris exchange precedes the continuity pair."""
    gateway = TopicEmbeddingGateway()
    selection = select_history_turns(
        _history(), "What is the capital of France again?", gateway
    )
    contents = [t.content for t in selection.turns]
    assert contents.index("What is the capital of France?") < contents.index(
        "What is the weather in Boston?"
    )
    assert contents == sorted(
        contents,
        key=lambda c: [t.content for t in _history()].index(c),
    )


def test_tool_results_never_drive_ranking():
    """A huge tool result stuffed with the query's topic words must not
    win its exchange a slot: ranking reads the user turn only."""
    gateway = TopicEmbeddingGateway()
    turns: list[Turn] = []
    turns += _exchange(
        "Show me invoice 1042",
        "Here is the invoice.",
        tool_results=(ToolResult(tool="lookup", result="france " * 500),),
    )
    turns += _exchange("What is the weather?", "Sunny.")
    selection = select_history_turns(turns, "Tell me about France", gateway)
    assert not selection.fallback_used
    # Invoice exchange is not relevant (its user text is about invoices)
    # and not the continuity pair, so it is dropped despite the tool
    # result being full of the word "france".
    assert "Show me invoice 1042" not in [t.content for t in selection.turns]


def test_selected_pairs_keep_tool_results_verbatim():
    """A selected exchange keeps its tool results intact in the copy
    handed to the gateway."""
    gateway = TopicEmbeddingGateway()
    result = ToolResult(tool="lookup", result="Paris data")
    turns = _exchange(
        "What is the capital of France?",
        "Paris.",
        tool_results=(result,),
    )
    selection = select_history_turns(turns, "capital of France?", gateway)
    assistant_turn = [t for t in selection.turns if t.role == ASSISTANT][0]
    assert assistant_turn.tool_results == (result,)


def test_empty_history_returns_empty_selection():
    selection = select_history_turns([], "hello", TopicEmbeddingGateway())
    assert selection == HistorySelection(
        turns=[], fallback_used=False, reason="empty history"
    )


def test_current_message_is_never_in_the_selection():
    """The caller appends the current user turn itself; the selector
    must not return it even if it matches history text."""
    gateway = TopicEmbeddingGateway()
    selection = select_history_turns(
        _history(), "What is the capital of France again?", gateway
    )
    assert "What is the capital of France again?" not in [
        t.content for t in selection.turns
    ]


def test_recency_mode_never_calls_embeddings():
    """Recency mode is the current default: even with a working
    gateway, no embedding call happens — relevance is on hold."""
    gateway = CountingEmbeddingGateway()
    selection = select_history_turns(
        _history(), "What is the capital of France again?", gateway, mode="recency"
    )
    assert gateway.calls == 0
    assert selection.fallback_used
    assert "recency mode" in selection.reason
    # Last 2 exchanges (invoice + weather), chronological.
    assert [t.content for t in selection.turns] == [
        "Show me invoice 1042",
        "Invoice 1042 totals $1,200.",
        "What is the weather in Boston?",
        "Boston is 72F and sunny.",
    ]


def test_invalid_mode_raises():
    with pytest.raises(ValueError, match="history selection mode"):
        select_history_turns(_history(), "anything", None, mode="random")
