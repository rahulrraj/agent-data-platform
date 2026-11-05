"""CLI: generate draft capability YAML from an API spec, and test it.

Generate (draft + optional questions):

    python -m agent_api.capability_import --from swagger \\
        --spec openapi.json --connection orders_api --out draft.yaml \\
        --questions questions.yaml

Output is a DRAFT for review, not a registry edit. Rewrite descriptions as
user needs (ADR 0004), then merge into registry/capabilities.yaml by hand.

`--validate` round-trips the draft through the registry loader
(YamlCapabilityRepository) and checks its ids against the live registry,
so loader errors and id collisions surface at generation time instead
of at app boot or, worse, as a silent overwrite (m-12). Validation
runs BEFORE anything is written: a draft with report errors or
validation problems writes no files and exits 2.

Test run (hermetic: keyword-embedding discovery + stub API, no model,
no network — see capability_import/testrun.py):

    python -m agent_api.capability_import --test-run \\
        --questions questions.yaml --spec openapi.json \\
        --registry path/to/capabilities.yaml

Exits 0 when every case passes, 1 when any case fails, 2 on a usage
or file error. Run it against the reviewed draft BEFORE merging, so a
bad description or request shape is caught while it is still cheap.

Regenerate questions from reviewed entries (after descriptions were
rewritten by hand — re-running generation from the spec would discard
the review edits):

    python -m agent_api.capability_import --entries draft.yaml \\
        --spec openapi.json --questions questions.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

import yaml

from agent_api.capability_import.swagger import capabilities_from_spec

_SOURCES = {"swagger": capabilities_from_spec}


class SpecLoadError(ValueError):
    """The spec file cannot be read or parsed."""


def load_spec(path: str) -> dict:
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError as exc:
        raise SpecLoadError(f"cannot read spec file {path!r}: {exc}") from exc
    if not text.strip():
        raise SpecLoadError(f"spec file {path!r} is empty")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SpecLoadError(
            f"spec file {path!r} is neither valid JSON nor YAML: {exc}"
        ) from exc
    if data is None:
        raise SpecLoadError(f"spec file {path!r} is empty")
    return data


def validate_draft(entries: list, registry_path: str) -> list[str]:
    """Validate a draft against the loader + live registry (m-12).

    Returns a list of problem strings (empty = valid). A draft that
    fails the loader, or whose ids collide with the live registry, is
    reported here — not discovered at app boot.
    """
    from agent_api.adapters.yaml_capability_repository import (
        YamlCapabilityRepository,
    )

    problems: list[str] = []
    draft_ids = [e.get("id") for e in entries]
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as fh:
            yaml.safe_dump({"capabilities": entries}, fh, sort_keys=False)
            tmp_path = fh.name
        try:
            YamlCapabilityRepository(tmp_path)
        finally:
            os.unlink(tmp_path)
    except Exception as exc:  # noqa: BLE001 — report, don't crash the CLI
        problems.append(f"draft fails registry load: {exc}")
        return problems
    if os.path.isfile(registry_path):
        try:
            live = YamlCapabilityRepository(registry_path)
            live_ids = {c.id for c in live.list_all()}
        except Exception as exc:  # noqa: BLE001
            problems.append(f"live registry unreadable: {exc}")
            return problems
        for draft_id in draft_ids:
            if draft_id in live_ids:
                problems.append(
                    f"id collision with live registry: {draft_id!r}"
                )
    else:
        print(
            f"  note: live registry {registry_path!r} not found; "
            "skipping collision check",
            file=sys.stderr,
        )
    return problems


def _default_registry_path(args) -> str:
    return args.registry or os.environ.get(
        "CAPABILITIES_PATH", "registry/capabilities.yaml"
    )


def _run_test_mode(args, parser) -> int:
    if not args.questions:
        parser.error("--test-run requires --questions (the question file to run)")
    if not args.spec:
        parser.error("--test-run requires --spec (the API spec, for the stub)")
    if args.source or args.connection or args.out:
        print(
            "  note: ignoring generation options in --test-run mode",
            file=sys.stderr,
        )
    try:
        spec = load_spec(args.spec)
    except SpecLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    from agent_api.capability_import.testrun import (
        print_report,
        run_question_file,
    )

    try:
        report = run_question_file(
            args.questions, _default_registry_path(args), spec
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — registry load failures land here
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print_report(report)
    return 0 if report["failed"] == 0 else 1


def _load_entries(path: str) -> list:
    """Entries from an existing draft/registry YAML file."""
    try:
        with open(path) as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        raise SpecLoadError(f"cannot read entries file {path!r}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise SpecLoadError(
            f"entries file {path!r} is not valid YAML: {exc}"
        ) from exc
    if not isinstance(data, dict) or not isinstance(
        data.get("capabilities"), list
    ):
        raise SpecLoadError(
            f"entries file {path!r} has no 'capabilities' list"
        )
    return data["capabilities"]


def _write_questions(entries, spec, args) -> int:
    from agent_api.capability_import.questions import questions_from_entries

    doc, notes = questions_from_entries(entries, spec, source_label=args.spec)
    with open(args.questions, "w") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False, default_flow_style=False)
    print(f"wrote {args.questions}", file=sys.stderr)
    for note in notes:
        print(f"  note: {note}", file=sys.stderr)
    print(
        f"{len(doc['cases'])} questions + {len(doc['controls'])} controls",
        file=sys.stderr,
    )
    return 0


def _run_entries_mode(args, parser) -> int:
    if args.source or args.connection or args.out:
        parser.error("--entries does not take --from, --connection or --out")
    if not args.questions:
        parser.error("--entries requires --questions (the file to write)")
    if not args.spec:
        parser.error("--entries requires --spec (the API spec, for examples)")
    try:
        spec = load_spec(args.spec)
        entries = _load_entries(args.entries)
    except SpecLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.validate:
        problems = validate_draft(entries, _default_registry_path(args))
        if problems:
            for problem in problems:
                print(f"  validate FAILED: {problem}", file=sys.stderr)
            return 2
        print("  validate: entries load and have no id collisions", file=sys.stderr)
    return _write_questions(entries, spec, args)
    if not args.questions:
        parser.error("--test-run requires --questions (the question file to run)")
    if not args.spec:
        parser.error("--test-run requires --spec (the API spec, for the stub)")
    if args.source or args.connection or args.out:
        print(
            "  note: ignoring generation options in --test-run mode",
            file=sys.stderr,
        )
    try:
        spec = load_spec(args.spec)
    except SpecLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    from agent_api.capability_import.testrun import run_question_file

    try:
        report = run_question_file(
            args.questions, _default_registry_path(args), spec
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — registry load failures land here
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for case in report["cases"]:
        label = f"{case['type']} {case['capability'] or 'control'}: {case['question']}"
        if case["passed"]:
            print(f"  PASS {label}", file=sys.stderr)
        else:
            print(f"  FAIL {label}", file=sys.stderr)
            for failure in case["failures"]:
                print(f"       {failure}", file=sys.stderr)
        for note in case["notes"]:
            print(f"       note: {note}", file=sys.stderr)
    print(
        f"{report['passed']} passed, {report['failed']} failed "
        f"of {report['total']} cases",
        file=sys.stderr,
    )
    return 0 if report["failed"] == 0 else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate draft capability YAML from an API spec, "
        "and run generated question files against a stub API."
    )
    parser.add_argument("--from", dest="source", choices=sorted(_SOURCES),
                        help="spec source (more later: snowflake)")
    parser.add_argument("--spec", help="path to the spec file")
    parser.add_argument("--connection",
                        help="DATA_CONNECTIONS key the capabilities will use")
    parser.add_argument("--out", default=None,
                        help="write draft YAML here (default: stdout)")
    parser.add_argument("--validate", action="store_true",
                        help="round-trip the draft through the registry "
                        "loader and check ids against the live registry "
                        "(exit 2 on problems; nothing is written)")
    parser.add_argument("--registry", default=None,
                        help="live registry path for --validate / --test-run "
                        "(default: $CAPABILITIES_PATH or "
                        "registry/capabilities.yaml)")
    parser.add_argument("--questions", default=None,
                        help="question file: written alongside the draft "
                        "in generate mode, written from --entries, "
                        "read in --test-run mode")
    parser.add_argument("--entries", default=None,
                        help="existing draft/registry YAML to generate "
                        "questions from (instead of --from); keeps "
                        "reviewed descriptions")
    parser.add_argument("--test-run", action="store_true",
                        help="run a question file against the registry + "
                        "a stub API built from the spec (no model, "
                        "no network)")
    args = parser.parse_args(argv)

    if args.test_run:
        return _run_test_mode(args, parser)

    if args.entries:
        return _run_entries_mode(args, parser)

    if args.source is None:
        parser.error("--from is required in generate mode")
    if args.spec is None:
        parser.error("--spec is required in generate mode")
    if args.connection is None:
        parser.error("--connection is required in generate mode")

    try:
        spec = load_spec(args.spec)
    except SpecLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    entries, report = _SOURCES[args.source](spec, args.connection)

    # Errors first: a broken spec writes nothing, whatever else was asked.
    for error in report["errors"]:
        print(f"  error: {error}", file=sys.stderr)
    if report["errors"]:
        return 2

    # Validation before writing: a draft that fails the loader or
    # collides with the live registry writes no files either.
    problems: list[str] = []
    if args.validate:
        problems = validate_draft(entries, _default_registry_path(args))
        if problems:
            for problem in problems:
                print(f"  validate FAILED: {problem}", file=sys.stderr)
            return 2

    draft = yaml.safe_dump({"capabilities": entries}, sort_keys=False,
                           default_flow_style=False)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(draft)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(draft)

    if args.questions:
        _write_questions(entries, spec, args)

    print(f"{report['count']} GET operations -> draft capabilities", file=sys.stderr)
    for skipped in report["skipped"]:
        print(f"  skipped (not GET): {skipped}", file=sys.stderr)
    for warning in report["warnings"]:
        print(f"  warning: {warning}", file=sys.stderr)
    if report["base_url"]:
        print(f"  spec base URL: {report['base_url']} "
              f"-> DATA_CONNECTIONS[{args.connection!r}]['base_url']", file=sys.stderr)
    print("Draft only: review, rewrite descriptions as user needs, "
          "then merge into registry/capabilities.yaml.", file=sys.stderr)
    if args.validate:
        print("  validate: draft loads and has no id collisions", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
