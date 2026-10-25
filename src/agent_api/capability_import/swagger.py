"""Build capability registry entries from an OpenAPI/Swagger document.

Scope (v1): OpenAPI 3.x and Swagger 2.0, GET operations only (the platform
is read-only), query + path parameters. Everything else is reported, not
guessed:

- non-GET operations -> skipped (listed in the report)
- header/cookie params -> skipped (the http adapter doesn't send them)
- unknown types -> string, with a warning
- missing summary/description -> generated "GET /path" description, with a
  warning to rewrite it as a user need

`capabilities_from_spec(spec, connection)` is pure: (entries, report).
Snowflake-table import will live in a sibling module with the same shape.
"""

from __future__ import annotations

import re

_TYPE_MAP = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
}

# m-6: spec text reaches the model verbatim through discovery results,
# so instruction-like phrasing in a third-party spec is a prompt
# injection vector with human review as the only control. These
# patterns are a cheap automated tripwire, not a verdict: a match adds
# a warning (reviewers must rewrite the description before merging),
# it never drops the capability silently.
_INSTRUCTION_PATTERNS = (
    re.compile(r"\b(ignore|disregard|override)\b.{0,40}\b(previous|prior|system)\b.{0,20}\binstructions?\b", re.I),
    re.compile(r"\breveal\b.{0,30}\b(system prompt|instructions?)\b", re.I),
    re.compile(r"\bdo not follow\b.{0,40}\b(instructions?|system)\b", re.I),
)


def _resolve_ref(ref: str, spec: dict):
    if not ref.startswith("#/"):
        raise ValueError(f"only local $refs are supported: {ref}")
    node = spec
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        node = node[part]
    return node


def _deref(node, spec: dict, _depth: int = 0):
    while isinstance(node, dict) and "$ref" in node:
        if _depth > 10:
            raise ValueError("cyclic $ref")
        node = _resolve_ref(node["$ref"], spec)
        _depth += 1
    return node


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


def _param_entry(param: dict, spec: dict):
    """Return (entry, warning). entry is None when the param is unsupported."""
    param = _deref(param, spec)
    name, location = param.get("name"), param.get("in")
    if location not in ("query", "path"):
        return None, f"parameter {name!r}: {location!r} params are not supported, skipped"
    schema = _deref(param.get("schema", param), spec)  # v3 nests, v2 inlines
    openapi_type = schema.get("type", "string")
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
    if servers and servers[0].get("url"):
        return servers[0]["url"]
    if spec.get("host"):
        scheme = (spec.get("schemes") or ["https"])[0]
        return f"{scheme}://{spec['host']}{spec.get('basePath', '')}"
    return None


def _response_fields(op: dict, spec: dict) -> list | None:
    """Property names of the 200-response object schema, if declared.

    Handles both a bare object schema and an array-of-objects schema.
    Returns None when no usable schema exists (fields stay unset and
    the reviewer adds them by hand, as before).
    """
    responses = op.get("responses") or {}
    response = responses.get("200") or responses.get(200) or {}
    response = _deref(response, spec) if isinstance(response, dict) else {}
    content = response.get("content") or {}
    for media in content.values():
        if not isinstance(media, dict):
            continue
        schema = _deref(media.get("schema") or {}, spec)
        if not isinstance(schema, dict):
            continue
        if schema.get("type") == "array":
            items = _deref(schema.get("items") or {}, spec)
            props = items.get("properties") if isinstance(items, dict) else None
        else:
            props = schema.get("properties")
        if isinstance(props, dict) and props:
            return list(props.keys())
    return None


def capabilities_from_spec(spec: dict, connection: str):
    """Return (entries, report) for the GET operations in an OpenAPI/Swagger doc."""
    entries: list = []
    report: dict = {"count": 0, "skipped": [], "warnings": [], "base_url": _base_url(spec)}
    used_ids: set = set()

    for path, path_item in (spec.get("paths") or {}).items():
        path_item = _deref(path_item, spec)
        if not isinstance(path_item, dict):
            continue
        shared_params = path_item.get("parameters", []) or []
        for method, op in path_item.items():
            if method == "parameters" or not isinstance(op, dict):
                continue
            if method.lower() != "get":
                report["skipped"].append(f"{method.upper()} {path}")
                continue
            op = _deref(op, spec)
            params: dict = {}
            for raw in list(shared_params) + list(op.get("parameters", []) or []):
                entry, warning = _param_entry(raw, spec)
                label = f"{method.upper()} {path}"
                if warning:
                    report["warnings"].append(f"{label}: {warning}")
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
                report["warnings"].append(
                    f"{capability_id}: no summary/description in spec — "
                    "description generated, rewrite it as a user need before merging"
                )
            # m-6: flag instruction-like phrasing anywhere in the text
            # that will reach the model (summary, description, param
            # descriptions). Warning only — review decides.
            spec_text = " ".join(
                [op.get("summary") or "", op.get("description") or ""]
                + [p["description"] for p in params.values()]
            )
            if any(p.search(spec_text) for p in _INSTRUCTION_PATTERNS):
                report["warnings"].append(
                    f"{capability_id}: description contains "
                    "instruction-like phrasing from the spec — rewrite "
                    "it as a user need before merging; spec text "
                    "reaches the model verbatim via discovery"
                )
            # m-12: generate `fields` from the 200-response schema when
            # one exists, so responses are trimmed before entering
            # model context instead of arriving untrimmed by default.
            fields = _response_fields(op, spec)
            request: dict = {"method": "GET", "path": path}
            if fields:
                request["fields"] = fields
            entries.append(
                {
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
            )

    report["count"] = len(entries)
    return entries, report
