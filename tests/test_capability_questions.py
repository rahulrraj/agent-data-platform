"""Tests for swagger -> test-question generation."""

from __future__ import annotations

import yaml

from agent_api.capability_import.questions import (
    _unknown_value,
    example_for_param,
    questions_from_entries,
)
from agent_api.capability_import.swagger import capabilities_from_spec

_SPEC = yaml.safe_load("""
    openapi: 3.0.0
    servers:
      - url: https://api.example.com/v1
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
                          properties: {order_id: {type: string}}
                      total: {type: integer}
      /health:
        get:
          operationId: healthCheck
          responses:
            '200':
              content:
                application/json:
                  schema:
                    type: object
                    properties: {ok: {type: boolean}}
""")


def _entries():
    entries, _ = capabilities_from_spec(_SPEC, "orders_api")
    return entries


def test_happy_and_paraphrase_use_spec_examples():
    doc, notes = questions_from_entries(_entries(), _SPEC, "spec.yaml")
    by_type = {(c["capability"], c["type"]): c for c in doc["cases"]}
    happy = by_type[("get_customer", "happy")]
    assert "C-123" in happy["question"]
    assert happy["params"] == {"customer_id": "C-123"}
    assert happy["expected"] == "match"
    assert happy["expect_result"] == "ok"
    paraphrase = by_type[("get_customer", "paraphrase")]
    assert paraphrase["question"].startswith("Find a customer's")
    assert paraphrase["params"] == {"customer_id": "C-123"}
    # the list op's param example beats the description's e.g. (same value here,
    # but the precedence is exercised by example_for_param below)
    assert by_type[("list_orders", "happy")]["params"]["customer_id"] == "C-123"
    # `limit` has no example anywhere: placeholder noted, not silent
    assert any("limit" in n and "placeholder" in n for n in notes)
    assert any("health_check" in n for n in notes)


def test_missing_param_drops_the_required_value():
    doc, _ = questions_from_entries(_entries(), _SPEC)
    case = next(
        c for c in doc["cases"]
        if c["capability"] == "get_customer" and c["type"] == "missing_param"
    )
    assert case["params"] == {}
    assert case["missing"] == "customer_id"
    assert "C-123" not in case["question"]
    assert "customer_id" in case["question"]  # names, no values
    assert case["expect_result"] == "missing_param"


def test_unknown_entity_derives_a_value_no_entity_carries():
    doc, _ = questions_from_entries(_entries(), _SPEC)
    case = next(
        c for c in doc["cases"]
        if c["capability"] == "get_customer" and c["type"] == "unknown_entity"
    )
    assert case["params"] == {"customer_id": "C-999"}
    assert "C-999" in case["question"]
    assert case["expect_result"] == "not_found"


def test_list_case_only_for_list_shaped_responses():
    doc, _ = questions_from_entries(_entries(), _SPEC)
    list_cases = [c for c in doc["cases"] if c["type"] == "list"]
    assert [c["capability"] for c in list_cases] == ["list_orders"]
    case = list_cases[0]
    assert case["question"].startswith("List all orders")
    assert "C-123" in case["question"]
    assert case["params"] == {"customer_id": "C-123", "limit": 10}


def test_placeholder_description_gets_no_questions():
    doc, notes = questions_from_entries(_entries(), _SPEC)
    assert not [c for c in doc["cases"] if c["capability"] == "health_check"]
    assert any("health_check" in n and "placeholder" in n for n in notes)


def test_controls_are_fixed_no_match_questions():
    doc, _ = questions_from_entries(_entries(), _SPEC)
    assert [c["question"] for c in doc["controls"]] == [
        "What's the weather in Boston?",
        "Tell me a joke",
        "What is the capital of France?",
    ]
    assert all(c["expected"] == "no_match" for c in doc["controls"])
    assert all(c["execute"] is False for c in doc["controls"])


def test_entry_without_a_spec_operation_is_noted_and_skipped():
    entries = _entries()
    entries.append({
        "id": "hand_written",
        "description": "Hand-written capability",
        "kind": "http",
        "connection": "orders_api",
        "mode": "read",
        "parameters": {},
        "request": {"method": "GET", "path": "/nowhere"},
    })
    doc, notes = questions_from_entries(entries, _SPEC)
    assert not [c for c in doc["cases"] if c["capability"] == "hand_written"]
    assert any("hand_written" in n for n in notes)


def test_example_precedence_and_placeholders():
    spec = {}
    param = {"name": "x", "in": "query", "example": "from-param",
             "schema": {"type": "string", "example": "from-schema"},
             "description": "X, e.g. from-desc"}
    assert example_for_param(param, spec) == ("from-param", True)
    param = {"name": "x", "in": "query",
             "schema": {"type": "string", "example": "from-schema"},
             "description": "X, e.g. from-desc"}
    assert example_for_param(param, spec) == ("from-schema", True)
    param = {"name": "x", "in": "query", "schema": {"type": "string"},
             "description": "X, e.g. from-desc"}
    assert example_for_param(param, spec) == ("from-desc", True)
    param = {"name": "n", "in": "query", "schema": {"type": "integer"},
             "description": "Count, e.g. 42"}
    assert example_for_param(param, spec) == (42, True)
    param = {"name": "x", "in": "query", "schema": {"type": "string"}}
    value, from_spec = example_for_param(param, spec)
    assert value == "example" and from_spec is False
    param = {"name": "x", "in": "query",
             "schema": {"type": "string", "enum": ["a", "b"]}}
    assert example_for_param(param, spec) == ("a", True)


def test_unknown_value_derivation():
    assert _unknown_value("C-123", "string") == "C-999"
    assert _unknown_value("ORD-98765", "string") == "ORD-99999"
    assert _unknown_value("plain", "string") == "no-such-value"
    assert _unknown_value(7, "integer") == 999999
    assert _unknown_value(1.5, "number") == 9999.5
    assert _unknown_value(True, "boolean") is False
