"""Dev-time generators: spec -> draft capability YAML.

Each source module exposes `capabilities_from_spec(spec, connection)` and
returns (entries, report). Output is ALWAYS a draft for human review — merge
it into registry/capabilities.yaml by hand after rewriting descriptions as
user needs (ADR 0004). Generators never edit the registry themselves.

`questions.py` generates test questions from the draft entries + the spec,
and `testrun.py` runs a question file against the registry and a stub API
built from the spec — the verification half of the skill's loop. `schemas.py`
holds the shared OpenAPI schema helpers both use.
"""

from __future__ import annotations
