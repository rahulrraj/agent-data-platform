"""Tool plugin system.

Each subfolder of this package is a plugin: a folder with a `tool.py` module.
Two flavors:

- Bare plugins expose @tool-decorated functions directly (e.g. calculate).
  They need no dependencies.
- Factory plugins expose `create_tool(deps)`, which builds the @tool
  function with its dependencies injected (e.g. discover_capability needs the
  capability registry and the embedding gateway). Factory plugins are skipped
  when load_tools() is called without deps.

Drop a new folder in and it is picked up automatically — no registry edits.

Tools live in this top-level package (rather than domain/) per ADR 0001:
the architecture reference's Agent profile names model/tool/memory as peer
building blocks, and `tools/` is where a contributor looks first.

Tool functions must stay technology-independent. A tool that needs outbound
I/O goes through a core-owned port (§4) — see AGENTS.md and
doc/data-capabilities.md.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolDeps:
    """Dependencies available to factory plugins. Fields are optional so a
    minimal setup (pure tools only) still works."""

    capability_registry: Any | None = None
    embedding_gateway: Any | None = None
    embedding_cache: Any | None = None
    capability_executors: dict[str, Any] = field(default_factory=dict)


def _is_strands_tool(obj: Any) -> bool:
    # @tool-decorated functions expose .tool_name (and stay callable).
    return callable(obj) and hasattr(obj, "tool_name")


def load_tools(deps: ToolDeps | None = None) -> list[Any]:
    """Discover and return every tool from every plugin folder, sorted by name.

    Factory plugins (exposing create_tool) are built with `deps`; they are
    skipped when deps is None.
    """
    tools: list[Any] = []
    for module_info in pkgutil.iter_modules(__path__):
        if not module_info.ispkg:
            continue
        tool_module = importlib.import_module(f"{__name__}.{module_info.name}.tool")
        create_tool = getattr(tool_module, "create_tool", None)
        if create_tool is not None:
            if deps is None:
                continue  # factory plugin needs dependencies
            tools.append(create_tool(deps))
            continue
        for attr_name in dir(tool_module):
            if attr_name.startswith("_"):
                continue
            candidate = getattr(tool_module, attr_name)
            if _is_strands_tool(candidate):
                tools.append(candidate)
    tools.sort(key=lambda t: t.tool_name)
    return tools


def tool_names(tools: list[Any] | None = None) -> list[str]:
    """Names of discovered tools (defaults to loading them)."""
    return [t.tool_name for t in (tools if tools is not None else load_tools())]
