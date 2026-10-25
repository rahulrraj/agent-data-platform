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

_TYPE_CHECKS = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
}


def _validate(capability, params: dict) -> str | None:
    """Return an error message, or None when params are valid."""
    if not isinstance(params, dict):
        return "parameters must be a JSON object"
    known = {p.name for p in capability.parameters}
    unknown = set(params) - known
    if unknown:
        return f"unknown parameters: {sorted(unknown)}"
    for p in capability.parameters:
        if p.name not in params:
            if p.required:
                return f"missing required parameter: {p.name}"
            continue
        expected = _TYPE_CHECKS.get(p.type, str)
        value = params[p.name]
        # bool is a subclass of int — exclude it from integer/number.
        if isinstance(value, bool) and p.type in ("integer", "number"):
            return f"parameter '{p.name}' must be {p.type}"
        if not isinstance(value, expected):
            return f"parameter '{p.name}' must be {p.type}"
    return None


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
        error = _validate(capability, parameters or {})
        if error:
            return f"Invalid parameters: {error}"
        executor = executors.get(capability.kind)
        if executor is None:
            return f"No executor configured for kind '{capability.kind}'."
        try:
            result = executor.execute(capability, parameters)
        except CapabilityExecutorError as exc:
            return f"Capability failed: {exc}"
        return json.dumps(result, default=str)

    return execute_capability
