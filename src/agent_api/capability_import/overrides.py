"""Description overrides for the capability generator.

A reviewed input, like the ``--entries`` file: a human writes it, the
generator only reads it — it NEVER writes to the overrides file, so a
hand-written description survives every re-run of the import
byte-for-byte. The file is keyed by generated capability id, so the
same override applies deterministically on every run from the same
spec (ids come from ``operationId``, snake_cased and uniquified —
see ``swagger._capability_id``).

Format (YAML)::

    overrides:
      get_customer_by_id:
        description: "Returns for all accounts of a customer"
        parameters:
          customer_id: "Customer ID, e.g. C-123"

Only descriptions are overridden — never ids, paths, types, or
``required``. A capability description replaces the Swagger
summary/description wholesale (including a generated ``GET /path``
placeholder, which then needs no "rewrite before merging" warning).
A parameter description replaces that parameter's spec description;
the parameter must exist on the capability, otherwise the override
is reported as a warning and ignored.

Unknown ids are a warning, not an error and not a crash: the spec
may have dropped or renamed the operation since the override was
written, and generation of the rest must still proceed. The warning
names the id so a typo is caught by reading the report.
"""

from __future__ import annotations

import yaml


class OverridesLoadError(ValueError):
    """The overrides file cannot be read or is not valid overrides YAML."""


def load_overrides(path: str) -> tuple[dict, list[str]]:
    """Load an overrides file.

    Returns ``(overrides, warnings)`` where ``overrides`` maps
    capability id -> ``{"description": str | None,
    "parameters": {name: description}}`` and ``warnings`` covers
    unknown keys that were ignored. Raises ``OverridesLoadError``
    for an unreadable file, invalid YAML, a wrong top-level shape,
    or a non-string description — the CLI turns that into exit 2
    and writes nothing.
    """
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError as exc:
        raise OverridesLoadError(
            f"cannot read overrides file {path!r}: {exc}"
        ) from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise OverridesLoadError(
            f"overrides file {path!r} is not valid YAML: {exc}"
        ) from exc
    if not isinstance(data, dict) or not isinstance(
        data.get("overrides"), dict
    ):
        raise OverridesLoadError(
            f"overrides file {path!r} has no 'overrides' mapping"
        )
    warnings: list[str] = []
    for key in data:
        if key != "overrides":
            warnings.append(
                f"overrides file: unknown top-level key {key!r}, ignored"
            )
    overrides: dict = {}
    for capability_id, raw in data["overrides"].items():
        if not isinstance(capability_id, str) or not capability_id:
            raise OverridesLoadError(
                "overrides file: capability id keys must be non-empty strings"
            )
        if not isinstance(raw, dict):
            raise OverridesLoadError(
                f"overrides file: override for {capability_id!r} "
                "is not a mapping"
            )
        description = raw.get("description")
        if description is not None and (
            not isinstance(description, str) or not description.strip()
        ):
            raise OverridesLoadError(
                f"overrides file: description for {capability_id!r} "
                "must be a non-empty string"
            )
        parameters = raw.get("parameters")
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict):
            raise OverridesLoadError(
                f"overrides file: parameters for {capability_id!r} "
                "is not a mapping"
            )
        for name, param_description in parameters.items():
            if not isinstance(name, str) or not name:
                raise OverridesLoadError(
                    f"overrides file: parameter names for {capability_id!r} "
                    "must be non-empty strings"
                )
            if not isinstance(param_description, str):
                raise OverridesLoadError(
                    f"overrides file: parameter {name!r} description for "
                    f"{capability_id!r} must be a string"
                )
        for key in raw:
            if key not in ("description", "parameters"):
                warnings.append(
                    f"overrides file: {capability_id}: unknown key "
                    f"{key!r}, ignored"
                )
        overrides[capability_id] = {
            "description": description,
            "parameters": dict(parameters),
        }
    return overrides, warnings


def apply_override(entry: dict, override: dict) -> list[str]:
    """Apply one capability's override to its generated entry, in place.

    Returns warnings (unknown parameter names). The entry's key order
    is unchanged — ``description`` is replaced in place and parameter
    descriptions are replaced in place — so the dumped YAML stays
    byte-identical across runs for the same spec + overrides.
    """
    warnings: list[str] = []
    description = override.get("description")
    if description is not None:
        entry["description"] = description
    for name, param_description in override.get("parameters", {}).items():
        params = entry.get("parameters") or {}
        if name in params:
            params[name]["description"] = param_description
        else:
            warnings.append(
                f"{entry.get('id')}: override names unknown parameter "
                f"{name!r}; ignored"
            )
    return warnings
