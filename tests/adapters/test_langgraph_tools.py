"""Adapter tests: Strands -> LangChain tool translation. No LLM needed."""

from agent_api.adapters.langgraph_base import to_langchain_tool
from agent_api.tools import load_tools, tool_names


def test_every_tool_translates():
    tools = load_tools()
    assert tools, "expected the pure tool plugins"
    for strands_tool in tools:
        lc_tool = to_langchain_tool(strands_tool)
        spec = strands_tool.tool_spec
        assert lc_tool.name == spec["name"]
        assert lc_tool.description == spec["description"]
        assert lc_tool.args == spec["inputSchema"]["json"]["properties"]


def test_translated_tool_invokes():
    tools = {t.tool_name: t for t in load_tools()}
    lc_tool = to_langchain_tool(tools["calculate"])
    assert lc_tool.invoke({"expression": "6 * 7"}) == "42"


def test_translated_names_match_tool_names():
    tools = load_tools()
    assert sorted(t.name for t in map(to_langchain_tool, tools)) == sorted(
        tool_names(tools)
    )
