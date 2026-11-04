"""Tests for the hermetic question-file test run (stub API + tools)."""

from __future__ import annotations

import json

import pytest
import yaml

from agent_api.capability_import.__main__ import main
from agent_api.capability_import.testrun import run_question_file

_SPEC = yaml.safe_load("""
    openapi: 3.0.0
    paths:
      /customers/{customer_id}:
        get:
          operationId: getCustomer
          summary: Look up a customer's name, tier, and region by customer ID
          parameters:
            - name: customer_id
              in: path
              required: true
              schema: {type: string}
              description: "Customer ID, e.g. C-123"
          responses:
            '200':
              content:
                application/json:
                  schema:
                    type: object
                    properties:
                      name: {type: string}
                      tier: {type: string}
                      region: {type: string}
      /customers/{customer_id}/orders:
        get:
          operationId: listOrders
          summary: List orders placed by a customer
          parameters:
            - name: customer_id
              in: path
              required: true
              schema: {type: string}
              example: C-123
            - name: limit
              in: query
              schema: {type: integer}
          responses:
            '200':
              content:
                application/json:
                  schema:
                    type: object
                    properties:
                      items:
                        type: array
                        items:
                          type: object
                          properties: {order_id: {type: string}, total: {type: number}}
                      total: {type: integer}
""")

_REGISTRY = """
capabilities:
  - id: get_customer
    description: "Look up a customer's name, tier, and region by customer ID"
    kind: http
    connection: orders_api
    mode: read
    parameters:
      customer_id: {type: string, required: true, description: "Customer ID, e.g. C-123"}
    request:
      method: GET
      path: "/customers/{customer_id}"
      fields: [name, tier, region]
  - id: list_orders
    description: "List orders placed by a customer"
    kind: http
    connection: orders_api
    mode: read
    parameters:
      customer_id: {type: string, required: true, description: "Customer ID, e.g. C-123"}
      limit: {type: integer, required: false, description: "Max orders to return"}
    request:
      method: GET
      path: "/customers/{customer_id}/orders"
      fields: [items, total]
  - id: returns_by_customer
    description: "Get investment returns for all accounts of a customer"
    kind: snowflake
    connection: snowflake_analytics
    mode: read
    parameters:
      customer_id: {type: string, required: true, description: "Customer ID, e.g. C-123"}
    query: "SELECT account_id, return_pct FROM fact_returns WHERE customer_id = %(customer_id)s"
"""

_GOOD_CASES = [
    {"capability": "get_customer", "type": "happy",
     "question": "Look up a customer's name, tier, and region by customer ID: customer_id C-123",
     "params": {"customer_id": "C-123"}, "expected": "match",
     "execute": True, "expect_result": "ok"},
    {"capability": "get_customer", "type": "paraphrase",
     "question": "Find a customer's name, tier, and region by customer ID: customer_id C-123",
     "params": {"customer_id": "C-123"}, "expected": "match",
     "execute": True, "expect_result": "ok"},
    {"capability": "get_customer", "type": "missing_param",
     "question": "Look up a customer's name, tier, and region by customer ID: customer_id",
     "params": {}, "expected": "match",
     "execute": True, "expect_result": "missing_param", "missing": "customer_id"},
    {"capability": "get_customer", "type": "unknown_entity",
     "question": "Look up a customer's name, tier, and region by customer ID: customer_id C-999",
     "params": {"customer_id": "C-999"}, "expected": "match",
     "execute": True, "expect_result": "not_found"},
    {"capability": "list_orders", "type": "list",
     "question": "List all orders: customer_id C-123, limit 10",
     "params": {"customer_id": "C-123", "limit": 10}, "expected": "match",
     "execute": True, "expect_result": "ok"},
    {"capability": "returns_by_customer", "type": "happy",
     "question": "What are the investment returns for all accounts of customer C-123?",
     "params": {"customer_id": "C-123"}, "expected": "match",
     "execute": True, "expect_result": "ok"},
]

_CONTROLS = [
    {"question": "What's the weather in Boston?"},
    {"question": "Tell me a joke"},
]


@pytest.fixture()
def files(tmp_path):
    registry = tmp_path / "registry.yaml"
    registry.write_text(_REGISTRY)
    questions = tmp_path / "questions.yaml"
    questions.write_text(yaml.safe_dump(
        {"version": 1, "cases": _GOOD_CASES, "controls": _CONTROLS},
        sort_keys=False,
    ))
    return registry, questions


def test_good_run_passes_every_case(files):
    registry, questions = files
    report = run_question_file(str(questions), str(registry), _SPEC)
    assert report["failed"] == 0, report["cases"]
    assert report["passed"] == report["total"] == 8
    snowflake = next(
        c for c in report["cases"] if c["capability"] == "returns_by_customer"
    )
    assert any("not stubbed" in n for n in snowflake["notes"])


def test_wrong_capability_fails_on_discovery(files, tmp_path):
    registry, _ = files
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(
        {"version": 1, "cases": [{
            "capability": "list_orders",
            "question": "Look up a customer's name, tier, and region by customer ID: customer_id C-123",
            "params": {"customer_id": "C-123"},
            "expected": "match", "execute": True, "expect_result": "ok",
        }], "controls": []},
        sort_keys=False,
    ))
    report = run_question_file(str(bad), str(registry), _SPEC)
    assert report["failed"] == 1
    assert "get_customer" in report["cases"][0]["failures"][0]


def test_control_that_matches_fails(files, tmp_path):
    registry, _ = files
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(
        {"version": 1, "cases": [], "controls": [
            {"question": "Look up a customer's name, tier, and region by customer ID: customer_id C-123"},
        ]},
        sort_keys=False,
    ))
    report = run_question_file(str(bad), str(registry), _SPEC)
    assert report["failed"] == 1


def test_malformed_question_file_raises(files, tmp_path):
    registry, _ = files
    bad = tmp_path / "bad.yaml"
    bad.write_text("cases: not-a-list\n")
    with pytest.raises(ValueError, match="must be lists"):
        run_question_file(str(bad), str(registry), _SPEC)
    missing = tmp_path / "missing.yaml"
    missing.write_text(yaml.safe_dump(
        {"version": 1, "cases": [{"question": "no capability here"}],
         "controls": []},
        sort_keys=False,
    ))
    with pytest.raises(ValueError, match="missing 'capability'"):
        run_question_file(str(missing), str(registry), _SPEC)


def test_missing_registry_raises(files):
    _, questions = files
    with pytest.raises(Exception, match="registry not found"):
        run_question_file(str(questions), "/nonexistent/registry.yaml", _SPEC)


def test_cli_test_run_exit_codes(files, tmp_path, capsys):
    registry, questions = files
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(_SPEC))
    rc = main(["--test-run", "--questions", str(questions),
               "--spec", str(spec_path), "--registry", str(registry)])
    assert rc == 0
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(
        {"version": 1, "cases": [{
            "capability": "list_orders",
            "question": "Look up a customer's name, tier, and region by customer ID: customer_id C-123",
            "params": {"customer_id": "C-123"},
            "expected": "match", "execute": True, "expect_result": "ok",
        }], "controls": []},
        sort_keys=False,
    ))
    rc = main(["--test-run", "--questions", str(bad),
               "--spec", str(spec_path), "--registry", str(registry)])
    assert rc == 1
    assert "1 failed" in capsys.readouterr().err


def test_cli_test_run_requires_questions_and_spec(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--test-run"])
    assert exc.value.code == 2
