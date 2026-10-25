"""Tool-result history seeding (M-13): prior execute results are stored
and re-seeded framework-natively instead of being dropped."""

from __future__ import annotations

from agent_api.adapters.langgraph_base import LangGraphGatewayBase
from agent_api.adapters.strands_base import StrandsGatewayBase
from agent_api.domain.conversation import (
    ASSISTANT,
    USER,
    Conversation,
    ToolResult,
    Turn,
)
from agent_api.platform.retry import RetryPolicy
from langchain_core.messages import AIMessage, ToolMessage


def _conv_with_results():
    return Conversation(
        id="s",
        turns=[
            Turn(role=USER, content="what tier?"),
            Turn(
                role=ASSISTANT,
                content="Gold tier",
                tool_uses=("execute_capability",),
                tool_results=(
                    ToolResult(tool="execute_capability", result='{"tier": "Gold"}'),
                ),
            ),
        ],
    )


class _CapturingAgent:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.messages = kwargs.get("messages", [])


class _StrandsProbe(StrandsGatewayBase):
    def _build_model(self):
        return object()

    def _provider_name(self):
        return "probe"


def test_strands_seeds_tool_use_and_tool_result_blocks(monkeypatch):
    monkeypatch.setattr(
        "agent_api.adapters.strands_base.Agent", _CapturingAgent
    )
    probe = _StrandsProbe(tools=[], system_prompt="s", retry_policy=RetryPolicy())
    agent = probe._build_agent(_conv_with_results())
    messages = agent.kwargs["messages"]
    assert messages[0] == {"role": USER, "content": [{"text": "what tier?"}]}
    assistant = messages[1]
    assert assistant["role"] == ASSISTANT
    assert {"text": "Gold tier"} in assistant["content"]
    use = [b["toolUse"] for b in assistant["content"] if "toolUse" in b]
    assert len(use) == 1 and use[0]["name"] == "execute_capability"
    results = messages[2]
    assert results["role"] == "user"
    outcome = [b["toolResult"] for b in results["content"] if "toolResult" in b]
    assert len(outcome) == 1
    assert outcome[0]["toolUseId"] == use[0]["toolUseId"]
    assert outcome[0]["content"] == {"text": '{"tier": "Gold"}'}


def test_strands_tool_results_extraction_pairs_in_order():
    agent = _CapturingAgent()
    agent.messages = [
        {"role": "assistant", "content": [{"toolUse": {"name": "execute_capability"}}]},
        {"role": "user", "content": [{"toolResult": {"content": {"text": "R1"}}}]},
        {"role": "assistant", "content": [{"toolUse": {"name": "calculate"}}]},
        {"role": "user", "content": [{"toolResult": {"content": {"text": "R2"}}}]},
    ]
    results = StrandsGatewayBase._tool_results(agent, skip=0)
    assert results == [
        ToolResult(tool="execute_capability", result="R1"),
        ToolResult(tool="calculate", result="R2"),
    ]


def test_langgraph_seeds_tool_messages():
    messages = LangGraphGatewayBase._messages(_conv_with_results())
    assert len(messages) == 3
    ai = messages[1]
    assert isinstance(ai, AIMessage)
    assert ai.tool_calls[0]["name"] == "execute_capability"
    tool_msg = messages[2]
    assert isinstance(tool_msg, ToolMessage)
    assert tool_msg.tool_call_id == ai.tool_calls[0]["id"]
    assert tool_msg.content == '{"tier": "Gold"}'


def test_langgraph_final_turn_reads_tool_results():
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"id": "t1", "name": "execute_capability", "args": {}}],
        ),
        ToolMessage(content='{"tier": "Gold"}', tool_call_id="t1"),
        AIMessage(content="Gold tier"),
    ]
    turn = LangGraphGatewayBase._final_turn(messages)
    assert turn.content == "Gold tier"
    assert turn.tool_uses == ("execute_capability",)
    assert turn.tool_results == (
        ToolResult(tool="execute_capability", result='{"tier": "Gold"}'),
    )
