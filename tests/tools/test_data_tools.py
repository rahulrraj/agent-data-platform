"""Tool tests: discover_capability + execute_capability with fakes.

The embedding fake returns [1, 0] for anything mentioning "customer" and
[0, 1] otherwise, so similarity is deterministic.
"""

import json

import pytest

from agent_api.domain.capability import Capability, ParameterSpec
from agent_api.tools import ToolDeps, load_tools, tool_names
from agent_api.tools.discover_capability.tool import create_tool as create_discover
from agent_api.tools.execute_capability.tool import create_tool as create_execute


def _caps():
    return [
        Capability(
            id="customer_by_id",
            description="Look up a customer's name, tier, and region by customer ID",
            kind="snowflake",
            connection="snowflake_analytics",
            mode="read",
            parameters=(
                ParameterSpec(name="customer_id", type="string", required=True,
                              description="Customer ID"),
            ),
            spec={"query": "SELECT ..."},
        ),
        Capability(
            id="order_status",
            description="Get an order's status and tracking number by order number",
            kind="http",
            connection="orders_api",
            mode="read",
            parameters=(
                ParameterSpec(name="order_number", type="string", required=True),
            ),
            spec={"request": {"method": "GET", "path": "/orders/{order_number}"}},
        ),
    ]


class FakeRegistry:
    def __init__(self, caps):
        self._caps = {c.id: c for c in caps}

    def list_all(self):
        return list(self._caps.values())

    def get(self, capability_id):
        return self._caps.get(capability_id)

    def ping(self):
        return None


class FakeEmbeddings:
    model_name = "fake-model"

    def embed(self, texts):
        return [
            [1.0, 0.0] if "customer" in t.lower() else [0.0, 1.0]
            for t in texts
        ]

    def ping(self):
        return None


class FakeExecutor:
    kind = "snowflake"

    def __init__(self):
        self.calls = []

    def execute(self, capability, params):
        self.calls.append((capability.id, params))
        return {"rows": [{"name": "Acme", "tier": "Gold"}]}


@pytest.fixture()
def deps():
    return ToolDeps(
        capability_registry=FakeRegistry(_caps()),
        embedding_gateway=FakeEmbeddings(),
        capability_executors={"snowflake": FakeExecutor(), "http": FakeExecutor()},
    )


def test_discover_finds_matching_capability(deps):
    discover = create_discover(deps)
    match = json.loads(discover("customer tier lookup"))
    assert match["id"] == "customer_by_id"
    assert match["parameters"]["customer_id"]["required"] is True


def test_discover_matches_second_capability(deps):
    discover = create_discover(deps)
    match = json.loads(discover("where is my order"))
    assert match["id"] == "order_status"


def test_discover_no_match_below_threshold():
    caps = _caps()

    class ZeroQuery(FakeEmbeddings):
        def embed(self, texts):
            # wiring call embeds descriptions; need calls get a zero vector
            if len(texts) > 1:
                return [[1.0, 0.0] for _ in texts]
            return [[0.0, 0.0]]

    deps = ToolDeps(
        capability_registry=FakeRegistry(caps),
        embedding_gateway=ZeroQuery(),
    )
    discover = create_discover(deps)
    result = discover("customer tier lookup")
    assert "No data capability matches" in result


def test_execute_runs_and_returns_json(deps):
    execute = create_execute(deps)
    out = json.loads(execute("customer_by_id", {"customer_id": "C-123"}))
    assert out["rows"][0]["tier"] == "Gold"


def test_execute_unknown_capability(deps):
    execute = create_execute(deps)
    assert "Unknown capability" in execute("nope", {})


def test_execute_missing_required_param(deps):
    execute = create_execute(deps)
    assert "missing required parameter" in execute("customer_by_id", {})


def test_execute_wrong_type(deps):
    execute = create_execute(deps)
    assert "must be string" in execute("customer_by_id", {"customer_id": 123})


def test_execute_unknown_param_rejected(deps):
    execute = create_execute(deps)
    assert "unknown parameters" in execute("customer_by_id",
                                           {"customer_id": "C-1", "drop": "x"})


def test_factory_tools_load_with_deps(deps):
    names = tool_names(load_tools(deps))
    assert "discover_capability" in names
    assert "execute_capability" in names
    assert "calculate" in names  # pure tools still load


def test_factory_tools_skipped_without_deps():
    names = tool_names(load_tools())
    assert "discover_capability" not in names
    assert "calculate" in names
