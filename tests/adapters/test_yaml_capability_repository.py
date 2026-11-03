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

# --- Result-trimming settings: load-time validation ---

_TRIM_ENTRY_HEAD = """  - id: x
    description: "x"
    kind: http
    connection: c
"""


def _trim_entry(tmp_path, extra_yaml):
    return _load_entry(tmp_path, _TRIM_ENTRY_HEAD + extra_yaml + """    request: {method: GET, path: "/a"}
""")


def test_trimming_settings_parsed_and_not_in_spec(tmp_path):
    repo = _trim_entry(
        tmp_path,
        """    list_paths: ["history.prices", "series"]
    keep: last
    max_results: 200
""",
    )
    cap = repo.get("x")
    assert cap.list_paths == ("history.prices", "series")
    assert cap.keep == "last"
    assert cap.max_results == 200
    assert "list_paths" not in cap.spec
    assert "keep" not in cap.spec
    assert "max_results" not in cap.spec
    assert cap.spec["request"]["path"] == "/a"


def test_trimming_settings_defaults(tmp_path):
    repo = _trim_entry(tmp_path, "")
    cap = repo.get("x")
    assert cap.list_paths == ()
    assert cap.keep == "first"
    assert cap.max_results == 50


@pytest.mark.parametrize("value", ["0", "-1", "501", "600"])
def test_max_results_out_of_range_rejected_at_load(tmp_path, value):
    with pytest.raises(CapabilityRepositoryError, match="max_results"):
        _trim_entry(tmp_path, f"    max_results: {value}\n")


@pytest.mark.parametrize(
    "value", ['"100"', "50.5", "true", "[100]", "{n: 100}"]
)
def test_max_results_wrong_type_rejected_at_load(tmp_path, value):
    with pytest.raises(CapabilityRepositoryError, match="max_results"):
        _trim_entry(tmp_path, f"    max_results: {value}\n")


@pytest.mark.parametrize("value", ['"newest"', '"LAST"', '"First"', "123", "true"])
def test_keep_rejected_at_load(tmp_path, value):
    with pytest.raises(CapabilityRepositoryError, match="'keep'"):
        _trim_entry(tmp_path, f"    keep: {value}\n")


@pytest.mark.parametrize(
    "value",
    [
        '"history.prices"',  # a bare string, not a list
        "42",
        "[\"\"]",  # empty-string entry
        "[123]",
        "[history.prices, \"\"]",
        "[history.prices, 7]",
        "{a: 1}",
    ],
)
def test_list_paths_rejected_at_load(tmp_path, value):
    with pytest.raises(CapabilityRepositoryError, match="list_paths"):
        _trim_entry(tmp_path, f"    list_paths: {value}\n")


def test_max_results_boundaries_accepted(tmp_path):
    for value in ("1", "500"):
        repo = _trim_entry(tmp_path, f"    max_results: {value}\n")
        assert repo.get("x").max_results == int(value)


# --- Token forwarding (ADR 0008): load-time validation ---

def test_forward_user_token_parsed_and_not_in_spec(tmp_path):
    repo = _trim_entry(tmp_path, "    forward_user_token: true\n")
    cap = repo.get("x")
    assert cap.forward_user_token is True
    assert "forward_user_token" not in cap.spec


def test_forward_user_token_defaults_false(tmp_path):
    repo = _trim_entry(tmp_path, "")
    assert repo.get("x").forward_user_token is False


@pytest.mark.parametrize("value", ['"true"', "1", "[true]", "{a: true}"])
def test_forward_user_token_non_bool_rejected_at_load(tmp_path, value):
    with pytest.raises(CapabilityRepositoryError, match="forward_user_token"):
        _trim_entry(tmp_path, f"    forward_user_token: {value}\n")


def test_forward_user_token_accepted_for_sql_kind(tmp_path):
    # Validate-but-ignore, like the trimming settings: the flag is
    # carried on the Capability, SQL executors never read it.
    repo = _load_entry(tmp_path, """  - id: x
    description: "x"
    kind: snowflake
    connection: c
    forward_user_token: true
    query: "SELECT 1"
""")
    cap = repo.get("x")
    assert cap.forward_user_token is True
    assert cap.spec["query"] == "SELECT 1"
