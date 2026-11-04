"""Generate draft capability YAML from a Swagger/OpenAPI spec (GET only).

The platform is read-only, so only GET operations become capabilities;
anything else is reported as skipped, never converted. Output is
ALWAYS a draft for human review — the generator never edits the
registry itself (see skills/capability-generator/SKILL.md).

Robustness contract: a malformed spec never crashes this module with
a traceback. A broken operation is dropped with an entry in
``report["errors"]`` (the CLI exits 2 and writes nothing); a
recoverable oddity becomes a ``report["warnings"]`` entry and the
operation is still generated. ``report`` therefore always carries
``count``, ``skipped``, ``warnings``, ``errors`` and ``base_url``.
"""

from __future__ import annotations

import re

from agent_api.capability_import.schemas import (
    deref,
    is_array_schema,
    schema_properties,
    schema_type,
)

_TYPE_MAP = {"string": "string", "integer": "integer", "number": "number",
             "boolean": "boolean"}

# m-6: spec text reaches the model verbatim through discovery, so
# instruction-like phrasing in a spec is flagged for the reviewer.
_INSTRUCTION_PATTERNS = [
    re.compile(r"\bignore (all )?(previous|prior|above) instructions\b", re.I),
    re.compile(r"\bdisregard\b.*\binstructions\b", re.I),
    re.compile(r"\breveal\b.*\b(system|internal|secret)\b", re.I),
    re.compile(r"\bdo not (tell|reveal|mention)\b", re.I),
]


def _snake(name: str) -> str:
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower()


def _capability_id(operation_id, method: str, path: str, used: set) -> str:
    if operation_id:
        base = _snake(operation_id)
    else:
        parts = [_snake(seg.strip("{}")) for seg in path.strip("/").split("/") if seg]
        base = "_".join([method.lower(), *parts]) or method.lower()
    base = re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]", "_", base)).strip("_") or "capability"
    candidate, n = base, 2
    while candidate in used:
        candidate, n = f"{base}_{n}", n + 1
    used.add(candidate)
    return candidate


def _param_entry(param, spec: dict):
    """Return (entry, warning). entry is None when the param is unsupported.

    Raises ValueError only when a ``$ref`` cannot be resolved: the
    spec is broken at that point and the whole operation is dropped
    with a report error rather than generated with a missing parameter.
    """
    if not isinstance(param, dict):
        return None, "parameter entry is not an object, skipped"
    param = deref(param, spec)
    if not isinstance(param, dict):
        return None, "parameter $ref does not resolve to an object, skipped"
    name, location = param.get("name"), param.get("in")
    if not name:
        return None, "parameter without a name, skipped"
    if location not in ("query", "path"):
        return None, f"parameter {name!r}: {location!r} params are not supported, skipped"
    schema = param.get("schema")
    if schema is None:
        # A parameter may carry its schema under `content` instead
        # (OpenAPI 3.x complex params): take the first media type's.
        content = param.get("content")
        if isinstance(content, dict):
            for media in content.values():
                if isinstance(media, dict) and "schema" in media:
                    schema = media["schema"]
                    break
        if schema is None:
            schema = param  # v3 nests, v2 inlines
    schema = deref(schema, spec)
    if not isinstance(schema, dict):
        return {
            "name": name,
            "type": "string",
            "required": bool(param.get("required", location == "path")),
            "description": param.get("description", ""),
        }, f"parameter {name!r}: schema is not an object, kept as string"
    openapi_type = schema_type(schema)
    ctype, warning = _TYPE_MAP.get(openapi_type), None
    if ctype is None:
        ctype = "string"
        warning = f"parameter {name!r}: type {openapi_type!r} is not mappable, kept as string"
    return {
        "name": name,
        "type": ctype,
        "required": bool(param.get("required", location == "path")),
        "description": param.get("description", ""),
    }, warning


def _base_url(spec: dict):
    servers = spec.get("servers") or []
    if isinstance(servers, list) and servers:
        first = servers[0]
        url = first.get("url") if isinstance(first, dict) else None
        if url:
            variables = first.get("variables") or {}
            if isinstance(variables, dict) and variables:
                def _expand(match):
                    default = variables.get(match.group(1))
                    if isinstance(default, dict) and default.get("default") is not None:
                        return str(default["default"])
                    return match.group(0)

                url = re.sub(r"\{(\w+)\}", _expand, url)
            return url
    if spec.get("host"):
        schemes = spec.get("schemes") or ["https"]
        scheme = schemes[0] if isinstance(schemes, list) and schemes else "https"
        return f"{scheme}://{spec['host']}{spec.get('basePath', '')}"
    return None


def _response_fields(op: dict, spec: dict) -> list | None:
    """Property names of the 200-response object schema, if declared.

    Handles a bare object schema, an array-of-objects schema, and
    composed schemas (``allOf``/``oneOf``/``anyOf``). Returns None
    when no usable schema exists (fields stay unset and the reviewer
    adds them by hand, as before). Raises ValueError when the schema
    graph is cyclic — the caller turns that into a warning.
    """
    responses = op.get("responses") or {}
    if not isinstance(responses, dict):
        return None
    response = responses.get("200") or responses.get(200) or {}
    response = deref(response, spec) if isinstance(response, dict) else {}
    if not isinstance(response, dict):
        return None
    content = response.get("content") or {}
    if not isinstance(content, dict):
        return None
    for media in content.values():
        if not isinstance(media, dict):
            continue
        schema = media.get("schema")
        if schema is None:
            continue
        props = schema_properties(schema, spec)
        if props:
            return list(props.keys())
    return None


def _envelope_list_prop(op: dict, spec: dict) -> str | None:
    """The single list property of an envelope response, if it has one.

    An object response with exactly one array property alongside
    scalar siblings (``{items: [...], total: 3}``) is an envelope:
    ``fields`` trims the envelope keys but never reaches the nested
    list, so the reviewer may want ``list_paths`` for it. Never
    raises: an unresolvable schema simply yields no hint.
    """
    try:
        responses = op.get("responses") or {}
        response = responses.get("200") or responses.get(200) or {}
        response = deref(response, spec) if isinstance(response, dict) else {}
        content = response.get("content") or {}
        if not isinstance(content, dict):
            return None
        for media in content.values():
            if not isinstance(media, dict) or "schema" not in media:
                continue
            props = schema_properties(media["schema"], spec)
            if not props:
                return None
            array_props = [
                name for name, sub in props.items()
                if is_array_schema(sub, spec)
            ]
            if len(array_props) == 1 and len(props) > 1:
                return array_props[0]
            return None
    except (ValueError, TypeError, AttributeError):
        return None
    return None


def _auth_warning(capability_id: str, op: dict, spec: dict) -> str | None:
    """Warn when an operation requires auth the connection must carry.

    Root-level ``security`` applies to operations that do not declare
    their own; an explicit empty list means no auth. Returns None
    when no auth is required or no schemes are named.
    """
    schemes: dict = {}
    components = spec.get("components")
    if isinstance(components, dict) and isinstance(
        components.get("securitySchemes"), dict
    ):
        schemes = components["securitySchemes"]
    elif isinstance(spec.get("securityDefinitions"), dict):
        schemes = spec["securityDefinitions"]
    required = op.get("security", spec.get("security"))
    if not required or not isinstance(required, list):
        return None
    names = []
    for requirement in required:
        if not isinstance(requirement, dict):
            continue
        for name in requirement:
            detail = schemes.get(name)
            scheme_type = detail.get("type") if isinstance(detail, dict) else None
            names.append(f"{name} ({scheme_type})" if scheme_type else name)
    if not names:
        return None
    return (
        f"{capability_id}: operation requires auth "
        f"({', '.join(names)}) — confirm the connection carries it"
    )


def capabilities_from_spec(spec, connection: str):
    """Return (entries, report) for the GET operations in an OpenAPI/Swagger doc.

    Never raises for a malformed spec: a non-object spec or a
    non-object ``paths`` yields an empty entry list with a report
    error, and a broken operation is dropped with its own report
    error while the rest of the spec still generates.
    """
    entries: list = []
    report: dict = {
        "count": 0,
        "skipped": [],
        "warnings": [],
        "errors": [],
        "base_url": None,
    }
    if not isinstance(spec, dict):
        report["errors"].append("spec is not a JSON/YAML object; nothing generated")
        return entries, report
    report["base_url"] = _base_url(spec)
    paths = spec.get("paths") or {}
    if not isinstance(paths, dict):
        report["errors"].append("'paths' is not an object; nothing generated")
        return entries, report
    if not paths:
        report["warnings"].append("spec declares no paths; nothing to generate")
    used_ids: set = set()

    for path, raw_path_item in paths.items():
        if not isinstance(path, str):
            report["errors"].append(f"path key {path!r} is not a string; skipped")
            continue
        try:
            path_item = deref(raw_path_item, spec)
        except ValueError as exc:
            report["errors"].append(f"{path}: {exc}; skipped")
            continue
        if not isinstance(path_item, dict):
            report["errors"].append(f"{path}: path item is not an object; skipped")
            continue
        shared_params = path_item.get("parameters", []) or []
        if not isinstance(shared_params, list):
            report["errors"].append(f"{path}: shared parameters are not a list; skipped")
            continue
        for method, op in path_item.items():
            if method == "parameters" or not isinstance(op, dict):
                continue
            if not isinstance(method, str):
                continue
            if method.lower() != "get":
                report["skipped"].append(f"{method.upper()} {path}")
                continue
            try:
                entry, warnings = _operation_entry(
                    method, path, op, spec, connection, used_ids, shared_params
                )
            except Exception as exc:  # noqa: BLE001 — one bad op must not kill the run
                report["errors"].append(f"{method.upper()} {path}: {exc}; skipped")
                continue
            report["warnings"].extend(warnings)
            entries.append(entry)

    report["count"] = len(entries)
    return entries, report


def _operation_entry(method, path, op, spec, connection, used_ids, shared_params):
    """Build one draft entry. Returns (entry, warnings).

    Raises ValueError when the operation itself is broken (an
    unresolvable ``$ref``); the caller drops the operation with a
    report error. A broken *response schema* only costs ``fields``,
    not the operation.
    """
    op = deref(op, spec)
    if not isinstance(op, dict):
        raise ValueError("operation is not an object after $ref resolution")
    warnings: list = []
    params: dict = {}
    raw_params = list(shared_params) + list(op.get("parameters", []) or [])
    for raw in raw_params:
        try:
            entry, warning = _param_entry(raw, spec)
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 — one bad param must not kill the op
            warnings.append(f"{method.upper()} {path}: unreadable parameter entry, skipped ({exc})")
            continue
        label = f"{method.upper()} {path}"
        if warning:
            warnings.append(f"{label}: {warning}")
        if entry is not None:
            params[entry["name"]] = entry  # operation-level wins
    description = (op.get("summary") or "").strip()
    generated = False
    if not description:
        first_line = (op.get("description") or "").strip().split("\n")[0].strip()
        description = first_line
    if not description:
        description, generated = f"{method.upper()} {path}", True
    capability_id = _capability_id(op.get("operationId"), method, path, used_ids)
    if generated:
        warnings.append(
            f"{capability_id}: no summary/description in spec — "
            "description generated, rewrite it as a user need before merging"
        )
    if op.get("deprecated") is True:
        warnings.append(f"{capability_id}: operation is deprecated in the spec")
    auth_warning = _auth_warning(capability_id, op, spec)
    if auth_warning:
        warnings.append(auth_warning)
    # m-6: flag instruction-like phrasing anywhere in the text
    # that will reach the model (summary, description, param
    # descriptions). Warning only — review decides.
    spec_text = " ".join(
        [op.get("summary") or "", op.get("description") or ""]
        + [p["description"] for p in params.values() if p.get("description")]
    )
    if any(p.search(spec_text) for p in _INSTRUCTION_PATTERNS):
        warnings.append(
            f"{capability_id}: description contains "
            "instruction-like phrasing from the spec — rewrite "
            "it as a user need before merging; spec text "
            "reaches the model verbatim via discovery"
        )
    # m-12: generate `fields` from the 200-response schema when
    # one exists, so responses are trimmed before entering
    # model context instead of arriving untrimmed by default.
    # A cyclic response schema only costs `fields`, not the op.
    fields = None
    try:
        fields = _response_fields(op, spec)
    except ValueError as exc:
        warnings.append(
            f"{capability_id}: response schema could not be resolved "
            f"({exc}); fields unset"
        )
    envelope_prop = _envelope_list_prop(op, spec)
    if envelope_prop:
        warnings.append(
            f"{capability_id}: response looks like an envelope; "
            f"consider list_paths: ['{envelope_prop}'] to trim the nested list"
        )
    request: dict = {"method": "GET", "path": path}
    if fields:
        request["fields"] = fields
    entry = {
        "id": capability_id,
        "description": description,
        "kind": "http",
        "connection": connection,
        "mode": "read",
        "parameters": {
            p["name"]: {
                "type": p["type"],
                "required": p["required"],
                "description": p["description"],
            }
            for p in params.values()
        },
        "request": request,
    }
    return entry, warnings
