"""Adapter tests: Snowflake executor with a stubbed connector module."""

import sys
import types

import pytest

from agent_api.adapters.snowflake_capability_executor import SnowflakeCapabilityExecutor
from agent_api.domain.capability import Capability, ParameterSpec
from agent_api.platform.retry import RetryPolicy
from agent_api.ports.capability_executor import CapabilityExecutorError

CONNECTIONS = {
    "snowflake_analytics": {
        "account": "acme",
        "user": "reader",
        "password": "pw",
        "warehouse": "wh",
        "database": "db",
    }
}

CUSTOMER_CAP = Capability(
    id="customer_by_id",
    description="Customer by ID",
    kind="snowflake",
    connection="snowflake_analytics",
    mode="read",
    parameters=(ParameterSpec(name="customer_id", type="string", required=True),),
    spec={"query": "SELECT name FROM customers WHERE id = %(customer_id)s"},
)


class FakeCursor:
    def __init__(self):
        self.executed = []
        self.description = [("name",)]

    def execute(self, query, args):
        self.executed.append((query, args))

    def fetchmany(self, n):
        return [("Acme Corp",)]

    def close(self):
        pass


class FakeConnection:
    def __init__(self):
        self.cursor_obj = FakeCursor()

    def cursor(self):
        return self.cursor_obj

    def close(self):
        pass


@pytest.fixture()
def fake_snowflake(monkeypatch):
    connector = types.ModuleType("snowflake.connector")
    connector.connect = lambda **kwargs: FakeConnection()
    package = types.ModuleType("snowflake")
    package.connector = connector
    monkeypatch.setitem(sys.modules, "snowflake", package)
    monkeypatch.setitem(sys.modules, "snowflake.connector", connector)
    return connector


@pytest.fixture()
def executor(fake_snowflake):
    return SnowflakeCapabilityExecutor(CONNECTIONS, RetryPolicy(max_attempts=1))


def test_parameterized_query_binds_by_name(executor, fake_snowflake):
    # capture the connection to inspect the executed query
    seen = {}
    orig_connect = fake_snowflake.connect
    fake_snowflake.connect = lambda **kw: seen.setdefault("conn", FakeConnection())

    result = executor.execute(CUSTOMER_CAP, {"customer_id": "C-123"})

    query, args = seen["conn"].cursor_obj.executed[0]
    assert query == "SELECT name FROM customers WHERE id = %(customer_id)s"
    assert args == {"customer_id": "C-123"}
    assert result == {"rows": [{"name": "Acme Corp"}], "truncated": False}


def test_rows_capped(executor, fake_snowflake, monkeypatch):
    many = [(f"name-{i}",) for i in range(60)]

    class ManyCursor(FakeCursor):
        def fetchmany(self, n):
            return many[:n]

    class ManyConnection(FakeConnection):
        def cursor(self):
            return ManyCursor()

    fake_snowflake.connect = lambda **kw: ManyConnection()
    result = executor.execute(CUSTOMER_CAP, {"customer_id": "C-1"})
    assert len(result["rows"]) == 50
    assert result["truncated"] is True


def test_non_read_mode_refused(executor):
    cap = Capability(
        id="x", description="x", kind="snowflake", connection="snowflake_analytics",
        mode="write", parameters=(), spec={"query": "DELETE FROM t"},
    )
    with pytest.raises(CapabilityExecutorError, match="non-read"):
        executor.execute(cap, {})
