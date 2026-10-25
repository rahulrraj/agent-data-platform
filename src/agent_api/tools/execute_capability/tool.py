"""execute_capability: run a data capability from the registry.

Takes a capability id (from discover_capability) and a parameters object.
Validates the parameters against the registry schema, then dispatches to the
executor for the capability's kind (snowflake, sqlserver, http).

Provenance is enforced, not just documented: the id must have been
returned by discover_capability (via the shared set on ToolDeps), or
the call is refused with an instruction to discover first. Hard reject,
not advisory: a model that guesses an id gets a correctable error, and
there is no false-rejection case for a model that follows the documented
discover-then-execute flow. Parameter *values* are deliberately NOT
checked against prior tool results: the first hop's values legitimately
come from the user's own message (e.g. "customer C-123"), so that check
would false-reject the primary flow.

Factory plugin: create_tool(deps) needs deps.capability_registry and
deps.capability_executors.
"""

from __future__ import annotations

import json

from strands import tool

from agent_api.ports.capability_executor import CapabilityExecutorError

def _coerce_value(p_type: str, value):
    """Coerce a model-supplied value to the registry type, or None.

    Safe cases only (m-15): "123" -> 123, "1.5" -> 1.5,
    "true"/"false" -> bool. A bool is never coerced to integer/number
    (bool subclasses int, so that check comes first), and nothing is
    coerced to string — a number where a string is declared is a model
    error worth one clean retry, not a silent conversion. Unknown
    registry types can no longer reach here: the registry loader
    rejects them (m-15), so the fallback below is unreachable in
    practice and fails closed.
    """
    if p_type == "integer":
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                return None
        return None
    if p_type == "number":
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return None
        return None
    if p_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        return None
    # string (and any defensive fallback): only a real string passes.
    return value if isinstance(value, str) else None


def _coerce(capability, params: dict) -> tuple[dict, str | None]:
    """Validate + coerce params. Returns (coerced, error-or-None)."""
    if not isinstance(params, dict):
        return {}, "parameters must be a JSON object"
    known = {p.name for p in capability.parameters}
    unknown = set(params) - known
    if unknown:
        return {}, f"unknown parameters: {sorted(unknown)}"
    coerced = {}
    for p in capability.parameters:
        if p.name not in params:
            if p.required:
                return {}, f"missing required parameter: {p.name}"
            continue
        value = _coerce_value(p.type, params[p.name])
        if value is None:
            # No declared type coerces to None, so a None result always
            # means the value (including a literal None) was invalid.
            return {}, f"parameter '{p.name}' must be {p.type}"
        coerced[p.name] = value
    return coerced, None


def create_tool(deps):
    registry = deps.capability_registry
    executors = deps.capability_executors or {}
    discovered = getattr(deps, "discovered_capability_ids", None)
    if registry is None:
        raise RuntimeError("execute_capability needs capability_registry")

    @tool
    def execute_capability(capability_id: str, parameters: dict) -> str:
        """Run an organization-data capability found via discover_capability.

        capability_id comes from discover_capability. parameters is a JSON
        object matching that capability's parameter schema, e.g.
        {"customer_id": "C-123"}. Returns the result as JSON.
        """
        capability = registry.get(capability_id)
        if capability is None:
            return f"Unknown capability: {capability_id}. Use discover_capability first."
        if discovered is not None and capability_id not in discovered:
            return (
                f"Capability '{capability_id}' was not returned by "
                "discover_capability. Use discover_capability first to find "
                "the right capability, then execute the id it returns."
            )
        coerced, error = _coerce(capability, parameters or {})
        if error:
            return f"Invalid parameters: {error}"
        executor = executors.get(capability.kind)
        if executor is None:
            return f"No executor configured for kind '{capability.kind}'."
        try:
            result = executor.execute(capability, coerced)
        except CapabilityExecutorError as exc:
            return f"Capability failed: {exc}"
        return json.dumps(result, default=str)

    return execute_capability
