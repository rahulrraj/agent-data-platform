"""CLI: generate draft capability YAML from an API spec.

    python -m agent_api.capability_import --from swagger \\
        --spec openapi.json --connection orders_api --out draft.yaml

Output is a DRAFT for review, not a registry edit. Rewrite descriptions as
user needs (ADR 0004), then merge into registry/capabilities.yaml by hand.
"""

from __future__ import annotations

import argparse
import json
import sys

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
