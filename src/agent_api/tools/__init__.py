"""Tool plugin system.

Each subfolder of this package is a plugin: a folder with a `tool.py` module
exposing one or more @tool-decorated functions. Drop a new folder in and it is
picked up automatically — no registry edits needed.

Tools live in this top-level package (rather than domain/) per ADR 0001:
the architecture reference's Agent profile names model/tool/memory as peer
building blocks, and `tools/` is where a contributor looks first.

Tool functions must stay technology-independent. If a future tool needs
outbound I/O, it must go through a core-owned port (§4) — see AGENTS.md.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any


def _is_strands_tool(obj: Any) -> bool:
    # @tool-decorated functions expose .tool_name (and stay callable).
    return callable(obj) and hasattr(obj, "tool_name")


def load_tools() -> list[Any]:
    """Discover and return every tool from every plugin folder, sorted by name."""
    tools: list[Any] = []
    for module_info in pkgutil.iter_modules(__path__):
        if not module_info.ispkg:
            continue
        tool_module = importlib.import_module(f"{__name__}.{module_info.name}.tool")
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
