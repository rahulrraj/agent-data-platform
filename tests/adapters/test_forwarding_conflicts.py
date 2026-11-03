"""Unit tests: validate_forwarding_conflicts (ADR 0008, decision 5).

The check itself is a standalone function; the composition root wires
the single call site at integration. These tests drive the function
directly: no registry file, no connections config beyond the dict
shape of DATA_CONNECTIONS.
"""

import pytest

from agent_api.adapters.yaml_capability_repository import (
    validate_forwarding_conflicts,
)
from agent_api.domain.capability import Capability
from agent_api.ports.capability_repository import CapabilityRepositoryError


def _cap(**overrides):
    kwargs = {
        "id": "order_status",
        "description": "x",
        "kind": "http",
        "connection": "orders_api",
        "mode": "read",
    }
    kwargs.update(overrides)
    return Capability(**kwargs)


def test_conflict_raises_naming_capability_and_connection():
    caps = [_cap(forward_user_token=True)]
    connections = {"orders_api": {"base_url": "https://api.example.com", "api_key": "secret"}}
    with pytest.raises(CapabilityRepositoryError) as excinfo:
        validate_forwarding_conflicts(caps, connections)
    message = str(excinfo.value)
    assert "order_status" in message
    assert "orders_api" in message
    assert "api_key" in message


def test_no_api_key_passes():
    caps = [_cap(forward_user_token=True)]
    connections = {"orders_api": {"base_url": "https://api.example.com"}}
    validate_forwarding_conflicts(caps, connections)


def test_forwarding_off_with_api_key_passes():
    caps = [_cap(forward_user_token=False)]
    connections = {"orders_api": {"base_url": "https://api.example.com", "api_key": "secret"}}
    validate_forwarding_conflicts(caps, connections)


def test_non_http_kind_ignored_even_with_api_key():
    caps = [
        _cap(
            kind="snowflake",
            connection="snowflake_analytics",
            forward_user_token=True,
            spec={"query": "SELECT 1"},
        )
    ]
    connections = {
        "snowflake_analytics": {"api_key": "secret"},
    }
    validate_forwarding_conflicts(caps, connections)


def test_missing_connection_passes_here():
    # A missing connection fails elsewhere (executor: not configured);
    # this check must not mask that with its own error.
    caps = [_cap(forward_user_token=True)]
    validate_forwarding_conflicts(caps, {})
    validate_forwarding_conflicts(caps, {"other_api": {}})


@pytest.mark.parametrize("api_key", ["", None, 0])
def test_empty_api_key_is_no_static_identity(api_key):
    caps = [_cap(forward_user_token=True)]
    connections = {"orders_api": {"base_url": "https://api.example.com", "api_key": api_key}}
    validate_forwarding_conflicts(caps, connections)


def test_second_capability_conflict_is_named():
    caps = [
        _cap(id="clean", forward_user_token=True),
        _cap(
            id="conflicted",
            connection="billing_api",
            forward_user_token=True,
        ),
    ]
    connections = {
        "orders_api": {"base_url": "https://api.example.com"},
        "billing_api": {"base_url": "https://billing.example.com", "api_key": "secret"},
    }
    with pytest.raises(CapabilityRepositoryError, match="conflicted"):
        validate_forwarding_conflicts(caps, connections)


def test_empty_inputs_pass():
    validate_forwarding_conflicts([], {})
