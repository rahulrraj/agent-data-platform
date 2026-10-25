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
    request: {method: GET, path: "/a"}
  - id: dup
    description: "second"
    kind: http
    connection: x
    parameters: {}
    request: {method: GET, path: "/b"}
"""
    )
    with pytest.raises(CapabilityRepositoryError, match="duplicate capability id"):
        YamlCapabilityRepository(str(path))


# --- m-18 / m-15 load-time validation (2026-10-25) ---

def _repo_with_entry(tmp_path, entry_yaml):
    path = tmp_path / "capabilities.yaml"
    path.write_text(f"capabilities:\n{entry_yaml}")
    return path


def _load_entry(tmp_path, entry_yaml):
    return YamlCapabilityRepository(str(_repo_with_entry(tmp_path, entry_yaml)))


def test_unknown_kind_rejected_at_load(tmp_path):
    with pytest.raises(CapabilityRepositoryError, match="unknown kind"):
        _load_entry(tmp_path, """  - id: x
    description: "x"
    kind: snowflke
    connection: c
    query: "SELECT 1"
""")


def test_missing_id_and_query_rejected_at_load(tmp_path):
    with pytest.raises(CapabilityRepositoryError, match="missing required field 'id'"):
        _load_entry(tmp_path, """  - description: "x"
    kind: snowflake
    connection: c
    query: "SELECT 1"
""")
    with pytest.raises(CapabilityRepositoryError, match="requires a 'query'"):
        _load_entry(tmp_path, """  - id: x
    description: "x"
    kind: snowflake
    connection: c
""")


def test_http_requires_request_path_and_unknown_mode_rejected(tmp_path):
    with pytest.raises(CapabilityRepositoryError, match="requires 'request.path'"):
        _load_entry(tmp_path, """  - id: x
    description: "x"
    kind: http
    connection: c
""")
    with pytest.raises(CapabilityRepositoryError, match="unknown mode"):
        _load_entry(tmp_path, """  - id: x
    description: "x"
    kind: http
    connection: c
    mode: write
    request: {method: GET, path: "/a"}
""")


def test_unknown_parameter_type_rejected_at_load(tmp_path):
    with pytest.raises(CapabilityRepositoryError, match="unknown type 'interger'"):
        _load_entry(tmp_path, """  - id: x
    description: "x"
    kind: http
    connection: c
    parameters:
      n:
        type: interger
        required: true
    request: {method: GET, path: "/a"}
""")
