"""Generate test questions from draft capability entries + the spec.

Deterministic templates only — no model, nothing invented. Every
value in a question comes from the spec (a parameter's ``example`` /
``examples`` / ``default``, the schema's ``example`` / ``enum``, or
the description's own "e.g. X"), or from a fixed type placeholder
when the spec gives no example at all. Params that used placeholders
are reported in ``notes`` so a reviewer can supply real examples.

Question types, per capability:

- ``happy`` — the description plus its example values. The pipeline
  smoke check: discovery must match, execution must succeed.
- ``paraphrase`` — the same need with the leading verb swapped
  (look up -> find, list -> show, ...). Same values, different
  surface words.
- ``missing_param`` — the description naming the parameters but
  carrying no values. Discovery must still match (it never sees
  values); execution must fail cleanly on the missing required
  parameter.
- ``unknown_entity`` — the happy question with the first path
  parameter replaced by a value no entity carries. Execution
  must map the stub's 404 cleanly. Only for entity lookups
  (operations with a path parameter).
- ``list`` — for list-shaped responses: "list all <collection>".
  Only when the 200-response schema is a list or an envelope
  holding one.

Plus fixed ``controls`` — questions that must match nothing
("What's the weather in Boston?", ...).

The output is a verification scaffold for the test run (see
testrun.py), not a quality claim about discovery: the questions
are only as varied as their templates, and the values in
``params`` are the values a *correct* extraction would produce —
extraction itself is the model's job and is not tested here.
"""

from __future__ import annotations

import re

from agent_api.capability_import.schemas import (
    deref,
    is_array_schema,
    schema_properties,
    schema_type,
)

_CONTROLS = [
    "What's the weather in Boston?",
    "Tell me a joke",
    "What is the capital of France?",
]

_VERB_SWAPS = (
    ("Look up", "Find"),
    ("Get", "What is"),
    ("List", "Show"),
    ("Retrieve", "Fetch"),
    ("Fetch", "Retrieve"),
    ("Show", "List"),
    ("Find", "Look up"),
    ("Return", "Give"),
)

_PAGINATION_PARAMS = {
    "limit": 10,
    "per_page": 10,
    "page_size": 10,
    "page": 1,
    "offset": 0,
}

_PLACEHOLDERS = {"string": "example", "integer": 123, "number": 1.5,
                 "boolean": True}

_EXAMPLE_RE = re.compile(r"e\.g\.?\s+([A-Za-z0-9][\w\-./]*)")


def _param_schema(param: dict, spec: dict):
    """The schema governing a parameter: v3 nested, v2 inline."""
    schema = param.get("schema")
    if schema is None:
        content = param.get("content")
        if isinstance(content, dict):
            for media in content.values():
                if isinstance(media, dict) and "schema" in media:
                    schema = media["schema"]
                    break
        if schema is None:
            schema = param
    try:
        schema = deref(schema, spec)
    except ValueError:
        return {}
    return schema if isinstance(schema, dict) else {}


def _coerce_example(raw: str, param_type: str):
    if param_type == "integer":
        try:
            return int(raw)
        except ValueError:
            return raw
    if param_type == "number":
        try:
            return float(raw)
        except ValueError:
            return raw
    if param_type == "boolean":
        if raw.lower() in ("true", "yes"):
            return True
        if raw.lower() in ("false", "no"):
            return False
    return raw


def example_for_param(param, spec: dict):
    """(value, came_from_spec) for one spec parameter object.

    Precedence: parameter ``example`` > ``examples`` > schema
    ``example`` / ``enum`` / ``default`` > parameter ``default`` >
    the description's "e.g. X" > a fixed type placeholder. The
    placeholder is the only invented value, and it is reported.
    """
    placeholder = ("example", False)
    if not isinstance(param, dict):
        return placeholder
    try:
        param = deref(param, spec)
    except ValueError:
        return placeholder
    if not isinstance(param, dict):
        return placeholder
    if param.get("example") is not None:
        return param["example"], True
    examples = param.get("examples")
    if isinstance(examples, dict) and examples:
        first = next(iter(examples.values()))
        if isinstance(first, dict) and first.get("value") is not None:
            return first["value"], True
    schema = _param_schema(param, spec)
    param_type = schema_type(schema)
    if schema.get("example") is not None:
        return schema["example"], True
    schema_examples = schema.get("examples")
    if isinstance(schema_examples, dict) and schema_examples:
        first = next(iter(schema_examples.values()))
        if isinstance(first, dict) and first.get("value") is not None:
            return first["value"], True
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        return schema["enum"][0], True
    if schema.get("default") is not None:
        return schema["default"], True
    if param.get("default") is not None:
        return param["default"], True
    match = _EXAMPLE_RE.search(param.get("description") or "")
    if match:
        return _coerce_example(match.group(1).rstrip(".,;"), param_type), True
    return _PLACEHOLDERS.get(param_type, "example"), False


def _unknown_value(example, param_type: str):
    """A value no entity carries, derived deterministically."""
    if isinstance(example, bool):
        return not example
    if isinstance(example, int):
        return 999999
    if isinstance(example, float):
        return 9999.5
    if isinstance(example, str):
        match = re.match(r"^(.*?)(\d+)$", example)
        if match:
            return match.group(1) + "9" * len(match.group(2))
        return "no-such-value"
    return "no-such-value"


def _paraphrase(description: str) -> str:
    for old, new in _VERB_SWAPS:
        if description.startswith(old + " "):
            return new + description[len(old):]
        if description == old:
            return new
    lowered = description[:1].lower() + description[1:] if description else description
    return f"Can you {lowered}"


def _suffix(pairs: dict) -> str:
    if not pairs:
        return ""
    return ": " + ", ".join(f"{name} {value}" for name, value in pairs.items())


def _operation_map(spec: dict):
    """(method, path) -> (operation, path item) for GET operations."""
    ops = {}
    paths = spec.get("paths") or {}
    if not isinstance(paths, dict):
        return ops
    for path, raw_item in paths.items():
        if not isinstance(path, str):
            continue
        try:
            item = deref(raw_item, spec)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method == "parameters" or not isinstance(op, dict):
                continue
            if not isinstance(method, str) or method.lower() != "get":
                continue
            try:
                op = deref(op, spec)
            except ValueError:
                continue
            if isinstance(op, dict):
                ops[(method.upper(), path)] = (op, item)
    return ops


def _spec_params(op: dict, path_item: dict, spec: dict) -> dict:
    """Parameter objects by name: shared + operation-level (op wins)."""
    params = {}
    raw_params = []
    shared = path_item.get("parameters", []) or []
    if isinstance(shared, list):
        raw_params.extend(shared)
    own = op.get("parameters", []) or []
    if isinstance(own, list):
        raw_params.extend(own)
    for raw in raw_params:
        try:
            param = deref(raw, spec)
        except ValueError:
            continue
        if isinstance(param, dict) and param.get("name"):
            params[param["name"]] = param
    return params


def _is_list_operation(op: dict, spec: dict) -> bool:
    """True when the 200 response is a list or an envelope holding one."""
    try:
        responses = op.get("responses") or {}
        if not isinstance(responses, dict):
            return False
        response = responses.get("200") or responses.get(200) or {}
        response = deref(response, spec) if isinstance(response, dict) else {}
        if not isinstance(response, dict):
            return False
        content = response.get("content") or {}
        if not isinstance(content, dict):
            return False
        for media in content.values():
            if not isinstance(media, dict) or "schema" not in media:
                continue
            schema = deref(media["schema"], spec)
            if is_array_schema(schema, spec):
                return True
            props = schema_properties(schema, spec)
            if props and any(
                is_array_schema(sub, spec) for sub in props.values()
            ):
                return True
    except ValueError:
        return False
    return False


def questions_from_entries(entries, spec: dict, source_label: str = ""):
    """Return (question document, notes) for generated entries.

    Entries whose description is the generator's own placeholder
    (``GET /path``) get no questions: there is no user-need text to
    vary yet. Rewrite the description first, then regenerate.
    """
    ops = _operation_map(spec if isinstance(spec, dict) else {})
    cases: list = []
    notes: list = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        request = entry.get("request") or {}
        method = request.get("method", "GET")
        path = request.get("path", "")
        op, path_item = ops.get((method, path), (None, None))
        if op is None:
            notes.append(
                f"{entry.get('id')}: no matching GET operation in the "
                "spec; no questions generated"
            )
            continue
        description = entry.get("description") or ""
        if description == f"{method} {path}":
            notes.append(
                f"{entry.get('id')}: description is a generated "
                "placeholder — rewrite it as a user need, then "
                "regenerate questions"
            )
            continue
        spec_params = _spec_params(op, path_item, spec)
        entry_params = entry.get("parameters") or {}
        values: dict = {}
        for name, param_spec in entry_params.items():
            spec_param = spec_params.get(name)
            if spec_param is not None:
                value, from_spec = example_for_param(spec_param, spec)
            elif isinstance(param_spec, dict):
                value, from_spec = example_for_param(
                    {"description": param_spec.get("description", "")}, spec
                )
            else:
                value, from_spec = "example", False
            values[name] = value
            if not from_spec:
                notes.append(
                    f"{entry.get('id')}: parameter {name!r} has no "
                    "example in the spec; the question file uses a "
                    "placeholder — supply a real example or edit the file"
                )
        capability_id = entry.get("id")
        suffix = _suffix(values)
        cases.append({
            "capability": capability_id,
            "type": "happy",
            "question": f"{description}{suffix}",
            "params": dict(values),
            "expected": "match",
            "execute": True,
            "expect_result": "ok",
        })
        cases.append({
            "capability": capability_id,
            "type": "paraphrase",
            "question": f"{_paraphrase(description)}{suffix}",
            "params": dict(values),
            "expected": "match",
            "execute": True,
            "expect_result": "ok",
        })
        required = [
            name for name, p in entry_params.items()
            if isinstance(p, dict) and p.get("required")
        ]
        if required:
            missing = required[0]
            # The question names the parameters but carries no values:
            # a value-less ask still has to resolve (discovery never
            # sees values), while execution must fail on the missing
            # required one. Names without values keep the question
            # resolvable even for the keyword-embedding stand-in,
            # where a bare description under-scores against a richly
            # described capability.
            cases.append({
                "capability": capability_id,
                "type": "missing_param",
                "question": f"{description}: {', '.join(values)}",
                "params": {
                    name: value for name, value in values.items()
                    if name != missing
                },
                "expected": "match",
                "execute": True,
                "expect_result": "missing_param",
                "missing": missing,
            })
        path_params = re.findall(r"\{(\w+)\}", path)
        if path_params:
            first = path_params[0]
            if first in values:
                first_spec = entry_params.get(first)
                param_type = (
                    first_spec.get("type", "string")
                    if isinstance(first_spec, dict) else "string"
                )
                unknown = _unknown_value(values[first], param_type)
                unknown_values = dict(values)
                unknown_values[first] = unknown
                cases.append({
                    "capability": capability_id,
                    "type": "unknown_entity",
                    "question": f"{description}{_suffix(unknown_values)}",
                    "params": unknown_values,
                    "expected": "match",
                    "execute": True,
                    "expect_result": "not_found",
                })
        if _is_list_operation(op, spec):
            collection = path.strip("/").split("/")[-1].strip("{}")
            collection = collection.replace("-", " ").replace("_", " ")
            list_params = {
                name: values[name] for name in path_params if name in values
            }
            for name, value in _PAGINATION_PARAMS.items():
                if name in entry_params and name not in list_params:
                    list_params[name] = value
            cases.append({
                "capability": capability_id,
                "type": "list",
                "question": f"List all {collection}{_suffix(list_params)}",
                "params": list_params,
                "expected": "match",
                "execute": True,
                "expect_result": "ok",
            })
    doc = {
        "version": 1,
        "generated_from": source_label,
        "cases": cases,
        "controls": [
            {"question": text, "expected": "no_match", "execute": False}
            for text in _CONTROLS
        ],
    }
    return doc, notes
