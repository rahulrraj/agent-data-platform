"""Read-only guards (M-7) and SQL Server positional binding (M-10)."""

from __future__ import annotations

import pytest

from agent_api.adapters.sqlserver_capability_executor import SqlServerCapabilityExecutor
from agent_api.domain.capability import Capability, ParameterSpec
from agent_api.platform.retry import RetryPolicy
from agent_api.ports.capability_executor import CapabilityExecutorError


def _cap(query, parameters=()):
    return Capability(
        id="c", description="c", kind="sqlserver", connection="missing",
        mode="read", parameters=parameters, spec={"query": query},
    )


def _executor():
    return SqlServerCapabilityExecutor({}, RetryPolicy(max_attempts=1))


def test_read_mode_delete_refused_before_connect():
    # M-7: no connection exists, so reaching the driver would fail with
    # "not configured" — the guard must fire first, with its own message.
    with pytest.raises(CapabilityExecutorError, match="SELECT/WITH"):
        _executor().execute(_cap("DELETE FROM orders WHERE id = 1"), {})


def test_read_mode_update_and_insert_refused():
    for query in (
        "UPDATE orders SET status = 'x'",
        "INSERT INTO orders VALUES (1)",
        "DROP TABLE orders",
        "-- comment\nDELETE FROM orders",
    ):
        with pytest.raises(CapabilityExecutorError, match="SELECT/WITH"):
            _executor().execute(_cap(query), {})


def test_select_and_with_pass_the_guard():
    # They fail later (no connection configured), not at the guard.
    for query in ("SELECT * FROM orders", "WITH t AS (SELECT 1) SELECT * FROM t"):
        with pytest.raises(CapabilityExecutorError, match="not configured"):
            _executor().execute(_cap(query), {})


def test_sqlserver_optional_keeps_positions():
    # M-10: a missing optional mid-list must not compress later values.
    params = (
        ParameterSpec(name="a", type="string", required=True),
        ParameterSpec(name="b", type="string", required=False),
        ParameterSpec(name="c", type="string", required=True),
    )
    cap = _cap("SELECT * FROM t WHERE a = ? AND b = ? AND c = ?", params)
    _, ordered = _executor()._placeholders(cap, {"a": "1", "c": "3"})
    assert ordered == ["1", None, "3"]


def test_sqlserver_missing_required_fails_closed():
    params = (ParameterSpec(name="a", type="string", required=True),)
    cap = _cap("SELECT * FROM t WHERE a = ?", params)
    with pytest.raises(CapabilityExecutorError, match="missing required parameter"):
        _executor()._placeholders(cap, {})


def test_sqlserver_placeholder_count_mismatch_rejected():
    params = (
        ParameterSpec(name="a", type="string", required=True),
        ParameterSpec(name="b", type="string", required=True),
    )
    cap = _cap("SELECT * FROM t WHERE a = ?", params)
    with pytest.raises(CapabilityExecutorError, match="placeholders"):
        _executor()._placeholders(cap, {"a": "1", "b": "2"})
