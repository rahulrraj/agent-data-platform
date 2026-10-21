"""Domain tests for the plugin loader (§9): pure discovery logic, no I/O."""

from agent_api.domain.plugins import load_tools, tool_names


def test_discovers_all_builtin_plugins():
    assert tool_names() == ["calculate", "get_current_time", "word_count"]


def test_loaded_tools_are_callable_with_names():
    tools = load_tools()
    assert len(tools) == 3
    assert all(callable(t) and isinstance(t.tool_name, str) for t in tools)


def test_calculate_plugin_works_standalone():
    tools = {t.tool_name: t for t in load_tools()}
    assert tools["calculate"](expression="6 * 7") == "42"
    assert tools["word_count"](text="the quick brown fox") == "4"
