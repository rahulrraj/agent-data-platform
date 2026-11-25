"""Relevance-based history window for the gateway handoff.

ON HOLD (Rahul, 2026-11-25): the relevance path below is implemented
and tested, but the service defaults to recency — embedding every
stored exchange on every turn is wasted work, and the vectors are not
stored anywhere, so each turn re-embeds the whole history. Relevance
selection (plus stored turn vectors, so a turn is embedded once) is a
separate PR; see TODO.md. Until then the default is the recency
fallback: the last 2 exchanges. Nothing here is deleted — the
relevance code stays, behind `mode="relevance"`.

Original design (applies when relevance is enabled):

Replaces the old "last N turns, blindly" window (AskAgentService used
to hand the gateway the last MAX_HISTORY_TURNS = 40 turns). Rahul's
direction: 2-3 turns is more than enough, and relevance beats recency —
rank the stored history against the current question and send only the
turns that earn their place. 40 stays as an absolute ceiling, never the
default.

Design decisions (see also the module docstring of ask_agent.py, which
still owns the handoff):

- Ranking unit: a user+assistant EXCHANGE (pair), not a single turn.
  A question without its answer is half a memory, and an answer without
  its question is ambiguous — the pair keeps them together. The window
  is therefore 2-3 exchanges (up to 6 turns), which is the pair-level
  reading of "2-3 turns".
- What is ranked: the USER turn's content only. The question carries
  the topic; the assistant's prose — and especially a tool result —
  must never win a slot on its own bulk. Tool results are never
  embedded, so a huge tool dump can neither dominate the ranking nor
  crowd the window on size.
- What is returned: tool results ride along verbatim inside the
  selected pairs (the gateway may need a prior tool output, and the
  handoff copy must stay faithful to the stored history). The pair cap
  bounds this: at most 3 pairs' worth of tool output can ever be in
  the window.
- Relevance cutoff: a pair must score at or above the embedding
  model's own ``similarity_threshold`` (same per-model convention as
  discover_capability — cosine scales differ between models, so the
  cutoff lives on the gateway, not here). Pairs below the cutoff are
  dropped even if they would fill the quota: an irrelevant past is
  noise, not context. If no pair clears the cutoff, only the
  continuity pair is sent.
- Continuity: the immediately preceding exchange is ALWAYS included
  (unless it is already among the relevant picks). A follow-up like
  "and the second one?" is meaningless without the turn it follows.
  Selection is therefore: top 2 relevant pairs + the last pair.
- Fallback: no gateway, an embedding failure, or a malformed embedding
  result falls back to recency — the last 2 exchanges — and records
  it: ``HistorySelection.fallback_used`` is True, ``reason`` names the
  cause, and a log line is emitted. Selection must never fail the
  turn: every failure path inside ``select_history_turns`` ends in the
  recency fallback, never an exception.
- Order: the returned turns are always in chronological (stored)
  order, regardless of ranking order — the gateway expects turns in
  order. The current user message is NOT part of the input or the
  output: the caller passes the turns *before* the current message
  as ``history_turns`` and the current message as ``current_message``,
  and appends the current user turn itself after selection (see the
  integration note in ``select_history_turns``).
- Ceiling: never more than ``max_turns`` turns (default 40, the
  existing MAX_HISTORY_TURNS). Selection normally returns at most 6,
  so the ceiling is a defensive bound, not a target.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agent_api.domain.capability import cosine_similarity
from agent_api.domain.conversation import ASSISTANT, USER, Turn
from agent_api.ports.embedding_gateway import (
    DEFAULT_SIMILARITY_THRESHOLD,
    EmbeddingGateway,
)

logger = logging.getLogger(__name__)

# Absolute ceiling on the handoff window (mirrors MAX_HISTORY_TURNS in
# ask_agent.py — the two must stay in sync; the ceiling is a bound,
# never a target: selection normally returns at most 6 turns).
ABSOLUTE_MAX_HISTORY_TURNS = 40

# How many of the most relevant exchanges to send, on top of the
# continuity pair. 2 relevant + 1 continuity = at most 3 exchanges.
DEFAULT_RELEVANT_PAIRS = 2

# How many exchanges the recency fallback sends.
FALLBACK_PAIRS = 2


@dataclass(frozen=True)
class HistorySelection:
    """The selected history window, plus how it was chosen.

    ``fallback_used`` / ``reason`` are the observability seam: the
    service (and tests) can tell a relevance pick from a recency
    fallback without parsing logs.
    """

    turns: list[Turn]
    fallback_used: bool
    reason: str


@dataclass(frozen=True)
class _Exchange:
    """One user turn plus the assistant turn(s) that answered it."""

    turns: tuple[Turn, ...]
    # Text actually embedded for ranking: the user turn's content only.
    rank_text: str


def _group_exchanges(history_turns: list[Turn]) -> list[_Exchange]:
    """Group stored turns into user+assistant exchanges, in order.

    A user turn starts a new exchange; following assistant turns attach
    to it. A leading assistant turn with no preceding user turn (should
    not happen in stored history) becomes a one-turn exchange of its
    own rather than being dropped.
    """
    exchanges: list[_Exchange] = []
    current: list[Turn] = []
    current_rank_text: str | None = None
    for turn in history_turns:
        if turn.role == USER:
            if current:
                exchanges.append(
                    _Exchange(
                        turns=tuple(current), rank_text=current_rank_text or ""
                    )
                )
            current = [turn]
            current_rank_text = turn.content
        elif turn.role == ASSISTANT and current:
            current.append(turn)
        else:
            if current:
                exchanges.append(
                    _Exchange(
                        turns=tuple(current), rank_text=current_rank_text or ""
                    )
                )
                current = []
                current_rank_text = None
            exchanges.append(_Exchange(turns=(turn,), rank_text=turn.content))
    if current:
        exchanges.append(
            _Exchange(turns=tuple(current), rank_text=current_rank_text or "")
        )
    return exchanges


def _recency_fallback(
    exchanges: list[_Exchange], reason: str
) -> HistorySelection:
    """Last FALLBACK_PAIRS exchanges, in chronological order."""
    logger.info("history selection fell back to recency: %s", reason)
    chosen = exchanges[-FALLBACK_PAIRS:] if exchanges else []
    return HistorySelection(
        turns=[t for ex in chosen for t in ex.turns],
        fallback_used=True,
        reason=reason,
    )


def select_history_turns(
    history_turns: list[Turn],
    current_message: str,
    embedding_gateway: EmbeddingGateway | None = None,
    *,
    mode: str = "relevance",
    max_relevant_pairs: int = DEFAULT_RELEVANT_PAIRS,
    max_turns: int = ABSOLUTE_MAX_HISTORY_TURNS,
) -> HistorySelection:
    """Pick the history turns to hand the gateway for this question.

    ``history_turns`` are the stored turns *before* the current user
    message; ``current_message`` is that message's text. The returned
    turns never include the current message — the caller appends the
    current user turn itself. (Integration: in ``AskAgentService.ask``
    / ``ask_stream``, call this on ``conversation.turns[:-1]`` with
    ``message``, then build the gateway conversation from the selected
    turns plus the final user turn.)

    ``mode`` is ``"relevance"`` (rank by embedding similarity, top
    ``max_relevant_pairs`` pairs above threshold plus the continuity
    pair) or ``"recency"`` (last 2 exchanges, no embedding call at
    all). The service defaults to recency — relevance is on hold
    pending the stored-vectors PR (see module docstring) — so pass
    ``mode`` explicitly when the caller wants relevance.

    Returns at most ``max_relevant_pairs`` relevant exchanges plus the
    immediately preceding exchange, in chronological order, and never
    more than ``max_turns`` turns. Never raises: any embedding problem
    degrades to the recency fallback.
    """
    if mode not in ("relevance", "recency"):
        raise ValueError(f"history selection mode must be 'relevance' or 'recency', got {mode!r}")
    exchanges = _group_exchanges(history_turns)
    if not exchanges:
        return HistorySelection(turns=[], fallback_used=False, reason="empty history")

    if mode == "recency":
        return _recency_fallback(exchanges, "recency mode: relevance selection on hold")

    if embedding_gateway is None:
        return _recency_fallback(exchanges, "no embedding gateway configured")

    try:
        texts = [current_message] + [ex.rank_text for ex in exchanges]
        vectors = embedding_gateway.embed(texts)
        if not vectors or len(vectors) != len(texts):
            return _recency_fallback(
                exchanges,
                f"embedding returned {0 if not vectors else len(vectors)} "
                f"vectors for {len(texts)} texts",
            )
        query_vector = vectors[0]
        threshold = getattr(
            embedding_gateway,
            "similarity_threshold",
            DEFAULT_SIMILARITY_THRESHOLD,
        )
        if threshold is None:
            threshold = DEFAULT_SIMILARITY_THRESHOLD
        scores = [
            (idx, cosine_similarity(query_vector, vec))
            for idx, vec in enumerate(vectors[1:], start=0)
        ]
    except Exception as exc:  # noqa: BLE001 — selection must never fail the turn
        return _recency_fallback(exchanges, f"embedding failed: {exc}")

    # Top relevant exchanges above the model's own threshold, best first.
    ranked = sorted(scores, key=lambda pair: pair[1], reverse=True)
    relevant = [idx for idx, score in ranked if score >= threshold][
        :max_relevant_pairs
    ]

    # Always keep the immediately preceding exchange for continuity.
    chosen_idx = set(relevant)
    chosen_idx.add(len(exchanges) - 1)
    chosen = sorted(chosen_idx)
    turns = [t for idx in chosen for t in exchanges[idx].turns]
    # Defensive ceiling only — selection is at most 3 exchanges, so
    # this truncates only if max_turns was lowered below that; keep
    # the most recent turns, which always include the continuity pair.
    if len(turns) > max_turns:
        turns = turns[-max_turns:]
    return HistorySelection(
        turns=turns,
        fallback_used=False,
        reason=(
            f"relevance: {len(relevant)} pair(s) above threshold "
            f"{threshold} plus continuity pair"
        ),
    )
