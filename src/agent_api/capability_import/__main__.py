"""CLI: generate draft capability YAML from an API spec.

    python -m agent_api.capability_import --from swagger \\
        --spec openapi.json --connection orders_api --out draft.yaml

Output is a DRAFT for review, not a registry edit. Rewrite descriptions as
user needs (ADR 0004), then merge into registry/capabilities.yaml by hand.

`--validate` round-trips the draft through the registry loader
(YamlCapabilityRepository) and checks its ids against the live registry,
so loader errors and id collisions surface at generation time instead
of at app boot or, worse, as a silent overwrite (m-12).
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


def load_spec(path: str) -> dict:
    with open(path) as fh:
        text = fh.read()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return yaml.safe_load(text)


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate draft capability YAML from an API spec."
    )
    parser.add_argument("--from", dest="source", required=True, choices=sorted(_SOURCES),
                        help="spec source (more later: snowflake)")
    parser.add_argument("--spec", required=True, help="path to the spec file")
    parser.add_argument("--connection", required=True,
                        help="DATA_CONNECTIONS key the capabilities will use")
    parser.add_argument("--out", default=None,
                        help="write draft YAML here (default: stdout)")
    parser.add_argument("--validate", action="store_true",
                        help="round-trip the draft through the registry "
                        "loader and check ids against the live registry "
                        "(exit 2 on problems)")
    parser.add_argument("--registry", default=None,
                        help="live registry path for --validate "
                        "(default: $CAPABILITIES_PATH or "
                        "registry/capabilities.yaml)")
    args = parser.parse_args(argv)

    spec = load_spec(args.spec)
    entries, report = _SOURCES[args.source](spec, args.connection)

    draft = yaml.safe_dump({"capabilities": entries}, sort_keys=False,
                           default_flow_style=False)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(draft)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(draft)

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
        registry_path = args.registry or os.environ.get(
            "CAPABILITIES_PATH", "registry/capabilities.yaml"
        )
        problems = validate_draft(entries, registry_path)
        if problems:
            for problem in problems:
                print(f"  validate FAILED: {problem}", file=sys.stderr)
            return 2
        print("  validate: draft loads and has no id collisions", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
