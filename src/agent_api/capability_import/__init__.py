"""Dev-time generators: spec -> draft capability YAML.

Each source module exposes `capabilities_from_spec(spec, connection)` and
returns (entries, report). Output is ALWAYS a draft for human review — merge
it into registry/capabilities.yaml by hand after rewriting descriptions as
user needs (ADR 0004). Generators never edit the registry themselves.
"""

from __future__ import annotations
