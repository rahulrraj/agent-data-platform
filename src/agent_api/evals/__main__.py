"""CLI: run the recorded eval set.

    python -m agent_api.evals

Runs the recorded cases in evals/ against the fixture registry + a stub
API built from the fixture spec. Hermetic: keyword-embedding discovery
plus a stub API, no model, no network (see
capability_import/testrun.py). Exits 0 when every case passes, 1 when
any case fails, 2 on a usage or file error.

`--cases` / `--registry` / `--spec` run a different set instead of the
recorded one — for developing new cases before recording them in
evals/. A set that is not green does not get recorded.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_api.capability_import.__main__ import SpecLoadError, load_spec
from agent_api.capability_import.testrun import print_report, run_question_file

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _default_cases() -> str:
    return str(_REPO_ROOT / "evals" / "cases.yaml")


def _default_registry() -> str:
    return str(_REPO_ROOT / "evals" / "registry.yaml")


def _default_spec() -> str:
    return str(_REPO_ROOT / "evals" / "spec.yaml")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the recorded eval set (evals/) against the "
        "fixture registry + a stub API. Hermetic: no model, no network."
    )
    parser.add_argument("--cases", default=None,
                        help="question file to run "
                        "(default: evals/cases.yaml)")
    parser.add_argument("--registry", default=None,
                        help="registry to run against "
                        "(default: evals/registry.yaml)")
    parser.add_argument("--spec", default=None,
                        help="API spec for the stub "
                        "(default: evals/spec.yaml)")
    args = parser.parse_args(argv)

    questions = args.cases or _default_cases()
    registry = args.registry or _default_registry()
    spec_path = args.spec or _default_spec()
    try:
        spec = load_spec(spec_path)
    except SpecLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        report = run_question_file(questions, registry, spec)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — registry load failures land here
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print_report(report)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
