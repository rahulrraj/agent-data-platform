"""Shared OpenAPI/Swagger schema helpers for the capability importers.

Pure functions over the parsed spec document: local ``$ref``
resolution, composed-schema property discovery, and example-value
synthesis for stub responses. Used by the swagger generator
(response ``fields``, envelope hints) and by the test-run stub
builder. Nothing here touches the registry or the network.

A ``ValueError`` means the spec is broken at that point (unresolvable
or cyclic reference); callers turn it into a report entry or a
warning — never a traceback.
"""

from __future__ import annotations

_MAX_REF_DEPTH = 10
_MAX_EXAMPLE_DEPTH = 2


def resolve_ref(ref: str, spec: dict):
    """Resolve a local ``#/…`` reference to the node it names."""
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise ValueError(f"only local $refs are supported: {ref!r}")
    node = spec
    try:
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            node = node[part]
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError(f"unresolvable $ref {ref!r}: {exc}") from exc
    return node


def deref(node, spec: dict, _depth: int = 0):
    """Follow a chain of local ``$ref``s to the node they name."""
    while isinstance(node, dict) and "$ref" in node:
        if _depth > _MAX_REF_DEPTH:
            raise ValueError("cyclic $ref")
        node = resolve_ref(node["$ref"], spec)
        _depth += 1
    return node


def schema_properties(schema, spec: dict, _depth: int = 0):
    """Property map of an object schema, or None when indeterminable.

    Merges ``allOf`` subschemas and unions ``oneOf``/``anyOf``
    options, and descends into array items, so composed and
    list-wrapped response schemas still yield their fields. A
    cyclic composition raises ``ValueError``; anything else
    unresolvable returns None.
    """
    if _depth > _MAX_REF_DEPTH:
        raise ValueError("cyclic schema composition")
    schema = deref(schema, spec)
    if not isinstance(schema, dict):
        return None
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((t for t in schema_type if t != "null"), None)
    if schema_type == "array":
        return schema_properties(schema.get("items") or {}, spec, _depth + 1)
    props: dict = {}
    own = schema.get("properties")
    if isinstance(own, dict):
        props.update(own)
    for subschemas in (schema.get("allOf") or [],):
        if not isinstance(subschemas, list):
            continue
        for sub in subschemas:
            sub_props = schema_properties(sub, spec, _depth + 1)
            if sub_props:
                props.update(sub_props)
    for key in ("oneOf", "anyOf"):
        options = schema.get(key) or []
        if not isinstance(options, list):
            continue
        for sub in options:
            sub_props = schema_properties(sub, spec, _depth + 1)
            if sub_props:
                for name, sub_schema in sub_props.items():
                    props.setdefault(name, sub_schema)
    return props or None


def schema_type(schema) -> str:
    """A schema's type, with OpenAPI 3.1 type arrays resolved.

    ``["string", "null"]`` means a nullable string: the first
    non-null member wins. An empty or absent type defaults to
    ``"string"``.
    """
    if not isinstance(schema, dict):
        return "string"
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        for candidate in schema_type:
            if candidate != "null":
                return candidate
        return "null"
    return schema_type or "string"


def is_array_schema(schema, spec: dict, _depth: int = 0) -> bool:
    if _depth > _MAX_REF_DEPTH:
        return False
    try:
        schema = deref(schema, spec)
    except ValueError:
        return False
    if not isinstance(schema, dict):
        return False
    schema_type = schema.get("type")
    types = schema_type if isinstance(schema_type, list) else [schema_type]
    if "array" in types:
        return True
    for key in ("allOf", "oneOf", "anyOf"):
        options = schema.get(key) or []
        if isinstance(options, list) and any(
            is_array_schema(sub, spec, _depth + 1) for sub in options        ):
            return True
    return False


def example_value(schema, spec: dict, _depth: int = 0):
    """A JSON-able example body for a response schema.

    Prefers the schema's own ``example``/``examples``/``enum``/``const``
    (the spec author's values), then synthesizes type placeholders.
    Objects recurse to a fixed depth; anything deeper becomes ``{}``.
    Deterministic: the same schema always yields the same body, so
    stub responses are stable across runs.
    """
    if _depth > _MAX_EXAMPLE_DEPTH:
        return {}
    try:
        schema = deref(schema, spec)
    except ValueError:
        return "example"
    if not isinstance(schema, dict):
        return "example"
    if schema.get("example") is not None:
        return schema["example"]
    examples = schema.get("examples")
    if isinstance(examples, dict) and examples:
        first = next(iter(examples.values()))
        if isinstance(first, dict) and first.get("value") is not None:
            return first["value"]
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        return schema["enum"][0]
    if schema.get("const") is not None:
        return schema["const"]
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((t for t in schema_type if t != "null"), "string")
    props = schema_properties(schema, spec)
    if props is not None:
        return {
            name: example_value(sub, spec, _depth + 1)
            for name, sub in props.items()
        }
    if schema_type == "array":
        return [example_value(schema.get("items") or {}, spec, _depth + 1)]
    if schema_type == "integer":
        return 123
    if schema_type == "number":
        return 1.5
    if schema_type == "boolean":
        return True
    return "example"
