"""Adapter tests: YAML capability registry parsing."""

import pytest

from agent_api.adapters.yaml_capability_repository import YamlCapabilityRepository
from agent_api.ports.capability_repository import CapabilityRepositoryError

SAMPLE = """
capabilities:
  - id: customer_by_id
    description: "Look up a customer by ID"
    kind: snowflake
    connection: snowflake_analytics
    mode: read
    parameters:
      customer_id:
        type: string
        required: true
        description: "Customer ID"
    query: "SELECT name FROM customers WHERE id = %(customer_id)s"
  - id: order_status
    description: "Order status by order number"
    kind: http
    connection: orders_api
    parameters:
      order_number:
        type: string
        required: true
    request:
      method: GET
      path: "/orders/{order_number}"
"""


@pytest.fixture()
def repo(tmp_path):
    path = tmp_path / "capabilities.yaml"
    path.write_text(SAMPLE)
    return YamlCapabilityRepository(str(path))


def test_lists_all_capabilities(repo):
    caps = repo.list_all()
    assert {c.id for c in caps} == {"customer_by_id", "order_status"}


def test_parses_parameters_and_spec(repo):
    cap = repo.get("customer_by_id")
    assert cap.kind == "snowflake"
    assert cap.connection == "snowflake_analytics"
    assert cap.mode == "read"
    assert len(cap.parameters) == 1
    param = cap.parameters[0]
    assert (param.name, param.type, param.required) == ("customer_id", "string", True)
    assert "query" in cap.spec


def test_get_unknown_returns_none(repo):
    assert repo.get("nope") is None


def test_missing_file_raises_clear_error(tmp_path):
    with pytest.raises(CapabilityRepositoryError, match="capability registry not found"):
        YamlCapabilityRepository(str(tmp_path / "missing.yaml"))


def test_duplicate_id_rejected(tmp_path):
    path = tmp_path / "capabilities.yaml"
    path.write_text(
        """
capabilities:
  - id: dup
    description: "first"
    kind: http
    connection: x
    parameters: {}
  - id: dup
    description: "second"
    kind: http
    connection: x
    parameters: {}
"""
    )
    with pytest.raises(CapabilityRepositoryError, match="duplicate capability id"):
        YamlCapabilityRepository(str(path))
