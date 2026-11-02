"""Bedrock-valid history replay for Strands gateways (ITEM 1).

The shared Strands base seeds the agent from stored history. Bedrock's
Converse API rejects the old shape (dict-form toolResult content, answer
text before the toolUse blocks), failing every later message after a
tool turn. These tests pin the corrected shape produced by the pure
``build_history_messages`` helper: list-form toolResult content, the
toolUse -> toolResult -> answer order, same-role merging, a leading
user message, and empty-text skipping. No live model calls.
"""

from __future__ import annotations

from agent_api.adapters.strands_base import build_history_messages
from agent_api.domain.conversation import (
    ASSISTANT,
    USER,
    Conversation,
    ToolResult,
    Turn,
)
from agent_api.platform.retry import RetryPolicy


def _tool_turn(content, *results):
    return Turn(
        role=ASSISTANT,
        content=content,
        tool_results=tuple(ToolResult(tool=tool, result=text) for tool, text in results),
    )


def _tool_result_blocks(message):
    return [b["toolResult"] for b in message["content"] if "toolResult" in b]


def _tool_use_blocks(message):
    return [b["toolUse"] for b in message["content"] if "toolUse" in b]


def test_tool_result_content_is_a_list_of_blocks():
    conv = Conversation(
        id="c",
        turns=[
            Turn(role=USER, content="run both"),
            _tool_turn(
                "done",
                ("discover_capability", "D"),
                ("execute_capability", "E"),
            ),
        ],
    )
    messages = build_history_messages(conv)
    results_msg = messages[2]
    assert results_msg["role"] == USER
    outcomes = _tool_result_blocks(results_msg)
    assert len(outcomes) == 2
    # List form, and each result pairs with its call via the synthetic id.
    assert outcomes[0] == {
        "toolUseId": "seed-1-0",
        "content": [{"text": "D"}],
    }
    assert outcomes[1] == {
        "toolUseId": "seed-1-1",
        "content": [{"text": "E"}],
    }
    uses = _tool_use_blocks(messages[1])
    assert [u["toolUseId"] for u in uses] == ["seed-1-0", "seed-1-1"]
    assert [u["name"] for u in uses] == [
        "discover_capability",
        "execute_capability",
    ]


def test_three_message_order_tool_use_then_result_then_answer():
    conv = Conversation(
        id="c",
        turns=[
            Turn(role=USER, content="what tier?"),
            _tool_turn("Gold tier", ("execute_capability", '{"tier": "Gold"}')),
        ],
    )
    messages = build_history_messages(conv)
    assert messages == [
        {"role": USER, "content": [{"text": "what tier?"}]},
        {
            "role": ASSISTANT,
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "seed-1-0",
                        "name": "execute_capability",
                        "input": {},
                    }
                }
            ],
        },
        {
            "role": USER,
            "content": [
                {
                    "toolResult": {
                        "toolUseId": "seed-1-0",
                        "content": [{"text": '{"tier": "Gold"}'}],
                    }
                }
            ],
        },
        {"role": ASSISTANT, "content": [{"text": "Gold tier"}]},
    ]
    # The toolUse message holds ONLY toolUse blocks: the answer text that
    # used to sit in front of the calls is in its own trailing message.
    assert all("toolUse" in b for b in messages[1]["content"])


def test_same_role_messages_are_merged():
    conv = Conversation(
        id="c",
        turns=[
            Turn(role=USER, content="first"),
            Turn(role=USER, content="second"),
            Turn(role=ASSISTANT, content="one"),
            Turn(role=ASSISTANT, content="two"),
        ],
    )
    messages = build_history_messages(conv)
    assert messages == [
        {"role": USER, "content": [{"text": "first"}, {"text": "second"}]},
        {"role": ASSISTANT, "content": [{"text": "one"}, {"text": "two"}]},
    ]


def test_follow_up_user_turn_follows_the_answer_message():
    conv = Conversation(
        id="c",
        turns=[
            Turn(role=USER, content="what tier?"),
            _tool_turn("Gold tier", ("execute_capability", '{"tier": "Gold"}')),
            Turn(role=USER, content="and the limit?"),
        ],
    )
    messages = build_history_messages(conv)
    # Transcript order is preserved: the answer message sits between the
    # toolResult message and the follow-up user turn, so roles alternate
    # without any merging across the answer.
    assert [m["role"] for m in messages] == [USER, ASSISTANT, USER, ASSISTANT, USER]
    assert messages[2]["content"] == [
        {
            "toolResult": {
                "toolUseId": "seed-1-0",
                "content": [{"text": '{"tier": "Gold"}'}],
            }
        }
    ]
    assert messages[3] == {"role": ASSISTANT, "content": [{"text": "Gold tier"}]}
    assert messages[4] == {"role": USER, "content": [{"text": "and the limit?"}]}


def test_leading_assistant_content_is_dropped_not_fabricated():
    conv = Conversation(
        id="c",
        turns=[
            Turn(role=ASSISTANT, content="orphaned summary"),
            Turn(role=USER, content="hi"),
        ],
    )
    messages = build_history_messages(conv)
    assert messages == [{"role": USER, "content": [{"text": "hi"}]}]


def test_leading_assistant_tool_turn_is_dropped_with_its_pair():
    conv = Conversation(
        id="c",
        turns=[
            _tool_turn("Gold tier", ("execute_capability", '{"tier": "Gold"}')),
            Turn(role=USER, content="follow-up"),
        ],
    )
    messages = build_history_messages(conv)
    # Dropping only the toolUse message would orphan its toolResult, so
    # the whole leading tool turn goes; the first message is the user turn.
    assert messages == [{"role": USER, "content": [{"text": "follow-up"}]}]
    assert not any("toolResult" in b for m in messages for b in m["content"])


def test_history_with_no_user_turn_is_empty():
    conv = Conversation(
        id="c",
        turns=[_tool_turn("Gold tier", ("execute_capability", "R"))],
    )
    assert build_history_messages(conv) == []


def test_leading_empty_user_text_does_not_orphan_tool_result():
    conv = Conversation(
        id="c",
        turns=[
            Turn(role=USER, content=""),
            _tool_turn("Gold tier", ("execute_capability", "R")),
            Turn(role=USER, content="hi"),
        ],
    )
    messages = build_history_messages(conv)
    assert messages == [{"role": USER, "content": [{"text": "hi"}]}]


def test_empty_text_blocks_are_skipped():
    conv = Conversation(
        id="c",
        turns=[
            Turn(role=USER, content=""),
            Turn(role=USER, content="what tier?"),
            Turn(role=ASSISTANT, content=""),
            _tool_turn("", ("execute_capability", "R")),
            Turn(role=USER, content="thanks"),
        ],
    )
    messages = build_history_messages(conv)
    assert not any(
        b == {"text": ""} for m in messages for b in m["content"]
    )
    # The empty-answer tool turn contributes only its toolUse/toolResult
    # pair; no empty trailing assistant message is emitted.
    assert messages == [
        {"role": USER, "content": [{"text": "what tier?"}]},
        {
            "role": ASSISTANT,
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "seed-3-0",
                        "name": "execute_capability",
                        "input": {},
                    }
                }
            ],
        },
        {
            "role": USER,
            "content": [
                {
                    "toolResult": {
                        "toolUseId": "seed-3-0",
                        "content": [{"text": "R"}],
                    }
                },
                {"text": "thanks"},
            ],
        },
    ]


def test_multi_turn_conversation_with_two_tool_turns():
    conv = Conversation(
        id="c",
        turns=[
            Turn(role=USER, content="q1"),
            _tool_turn("a1", ("calculate", "1")),
            Turn(role=USER, content="q2"),
            _tool_turn(
                "a2",
                ("discover_capability", "D"),
                ("execute_capability", "E"),
            ),
            Turn(role=USER, content="q3"),
        ],
    )
    messages = build_history_messages(conv)
    assert len(messages) == 9
    roles = [m["role"] for m in messages]
    assert roles == [
        USER, ASSISTANT, USER, ASSISTANT, USER,
        ASSISTANT, USER, ASSISTANT, USER,
    ]
    # Roles alternate throughout (no back-to-back same-role messages).
    assert all(a != b for a, b in zip(roles, roles[1:]))
    assert messages[0]["content"] == [{"text": "q1"}]
    assert messages[3]["content"] == [{"text": "a1"}]
    assert messages[4]["content"] == [{"text": "q2"}]
    assert messages[7]["content"] == [{"text": "a2"}]
    assert messages[8]["content"] == [{"text": "q3"}]
    # First tool turn: single paired call/result.
    assert _tool_use_blocks(messages[1])[0]["toolUseId"] == "seed-1-0"
    assert _tool_result_blocks(messages[2])[0]["toolUseId"] == "seed-1-0"
    assert _tool_result_blocks(messages[2])[0]["content"] == [{"text": "1"}]
    # Second tool turn: two paired calls/results, ids keyed to turn index.
    uses = _tool_use_blocks(messages[5])
    outcomes = _tool_result_blocks(messages[6])
    assert [u["toolUseId"] for u in uses] == ["seed-3-0", "seed-3-1"]
    assert [o["toolUseId"] for o in outcomes] == ["seed-3-0", "seed-3-1"]
    assert [o["content"] for o in outcomes] == [[{"text": "D"}], [{"text": "E"}]]


def test_empty_conversation_yields_no_messages():
    assert build_history_messages(Conversation(id="c", turns=[])) == []


def test_build_agent_seeds_with_helper_messages(monkeypatch):
    from agent_api.adapters.strands_base import StrandsGatewayBase

    class _CapturingAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _Probe(StrandsGatewayBase):
        def _build_model(self):
            return object()

        def _provider_name(self):
            return "probe"

    monkeypatch.setattr(
        "agent_api.adapters.strands_base.Agent", _CapturingAgent
    )
    conv = Conversation(
        id="c",
        turns=[
            Turn(role=USER, content="what tier?"),
            _tool_turn("Gold tier", ("execute_capability", "R")),
        ],
    )
    probe = _Probe(tools=[], system_prompt="s", retry_policy=RetryPolicy())
    agent = probe._build_agent(conv)
    assert agent.kwargs["messages"] == build_history_messages(conv)
