"""Tests for the recorded eval set (evals/ + `python -m agent_api.evals`).

The recorded set is the durable regression net for capability
behavior (lifecycle doc §5, Evaluate). These tests pin the runner
itself: the recorded set passes green, a corrupted expectation
fails, and the CLI exit codes hold. The set's content lives in
evals/ — a behavior change updates the cases there, deliberately,
in the same change.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
import yaml

from agent_api.capability_import.testrun import print_report, run_question_file
from agent_api.evals.__main__ import main

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CASES = _REPO_ROOT / "evals" / "cases.yaml"
_REGISTRY = _REPO_ROOT / "evals" / "registry.yaml"
_SPEC = _REPO_ROOT / "evals" / "spec.yaml"


def _load_spec():
    from agent_api.capability_import.__main__ import load_spec

    return load_spec(str(_SPEC))


def test_recorded_set_passes_green():
    report = run_question_file(str(_CASES), str(_REGISTRY), _load_spec())
    assert report["failed"] == 0, report["cases"]
    assert report["passed"] == report["total"] == 12
    trimming = next(
        c for c in report["cases"] if c["capability"] == "list_customers"
    )
    assert trimming["passed"]
    snowflake = next(
        c for c in report["cases"] if c["capability"] == "returns_by_customer"
    )
    assert any("not stubbed" in n for n in snowflake["notes"])


def test_recorded_set_bites_on_a_corrupted_expectation(tmp_path):
    bad = tmp_path / "cases.yaml"
    doc = yaml.safe_load(_CASES.read_text())
    doc["cases"][6]["capability"] = "get_customer"  # list_customers case
    bad.write_text(yaml.safe_dump(doc, sort_keys=False))
    report = run_question_file(str(bad), str(_REGISTRY), _load_spec())
    assert report["failed"] == 1
    assert "list_customers" in report["cases"][6]["failures"][0]


def test_wrong_item_count_fails(tmp_path):
    bad = tmp_path / "cases.yaml"
    doc = yaml.safe_load(_CASES.read_text())
    trimming = next(
        c for c in doc["cases"] if c["capability"] == "list_customers"
    )
    trimming["expect_items"] = 3  # the stub serves 3, the cap trims to 2
    bad.write_text(yaml.safe_dump(doc, sort_keys=False))
    report = run_question_file(str(bad), str(_REGISTRY), _load_spec())
    assert report["failed"] == 1
    assert "expected 3 items, got 2" in report["cases"][6]["failures"][0]


def test_expect_items_validation(tmp_path):
    bad = tmp_path / "cases.yaml"
    bad.write_text(yaml.safe_dump(
        {"version": 1, "cases": [{
            "capability": "get_customer",
            "question": "Look up a customer's name, tier, and region by customer ID: customer_id C-123",
            "params": {"customer_id": "C-123"},
            "expected": "match", "execute": True,
            "expect_result": "missing_param", "missing": "customer_id",
            "expect_items": 1,
        }], "controls": []},
        sort_keys=False,
    ))
    with pytest.raises(ValueError, match="needs execute \\+ expect_result 'ok'"):
        run_question_file(str(bad), str(_REGISTRY), _load_spec())
    bad.write_text(yaml.safe_dump(
        {"version": 1, "cases": [{
            "capability": "list_customers",
            "question": "List every customer record in the directory",
            "params": {},
            "expected": "match", "execute": True, "expect_result": "ok",
            "expect_items": "two",
        }], "controls": []},
        sort_keys=False,
    ))
    with pytest.raises(ValueError, match="bad expect_items"):
        run_question_file(str(bad), str(_REGISTRY), _load_spec())


def test_cli_exit_codes(tmp_path):
    assert main([]) == 0
    bad = tmp_path / "cases.yaml"
    doc = yaml.safe_load(_CASES.read_text())
    doc["cases"][0]["capability"] = "list_orders"
    bad.write_text(yaml.safe_dump(doc, sort_keys=False))
    assert main(["--cases", str(bad)]) == 1
    assert main(["--cases", str(tmp_path / "missing.yaml")]) == 2


def test_print_report():
    report = {
        "passed": 1, "failed": 1, "total": 2,
        "cases": [
            {"capability": "get_customer", "type": "happy",
             "question": "Look up a customer", "passed": True,
             "failures": [], "notes": []},
            {"capability": "list_orders", "type": "list",
             "question": "List orders", "passed": False,
             "failures": ["discovery returned no match"], "notes": []},
        ],
    }
    buf = io.StringIO()
    print_report(report, file=buf)
    out = buf.getvalue()
    assert "PASS happy get_customer: Look up a customer" in out
    assert "FAIL list list_orders: List orders" in out
    assert "discovery returned no match" in out
    assert "1 passed, 1 failed of 2 cases" in out
