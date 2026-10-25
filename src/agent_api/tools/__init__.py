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

from agent_api.ports.capability_executor import CapabilityExecutor
from agent_api.ports.capability_repository import CapabilityRepository
from agent_api.ports.embedding_cache import EmbeddingCache
from agent_api.ports.embedding_gateway import EmbeddingGateway


@dataclass
class ToolDeps:
    """Dependencies available to factory plugins. Fields are optional so a
    minimal setup (pure tools only) still works.

    `discovered_capability_ids` is the provenance set shared by the
    discover/execute tools: discover_capability records every id it
    returns, execute_capability refuses ids outside the set. Scope is
    the process (the tool layer has no turn-boundary hook to reset it
    per turn); a restart clears it. It still defeats invented ids and
    skipped discover hops, which is the failure it guards.
    """

    capability_registry: CapabilityRepository | None = None
    embedding_gateway: EmbeddingGateway | None = None
    embedding_cache: EmbeddingCache | None = None
    capability_executors: dict[str, CapabilityExecutor] = field(default_factory=dict)
    discovered_capability_ids: set[str] = field(default_factory=set)


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
        # Only tools DEFINED in this plugin module: a @tool function
        # re-exported via import lives in the module's __dict__ too, so
        # the __dict__ test alone would still register it. The
        # __module__ check is the test that actually excludes re-exports
        # (a re-exported tool's __module__ is its home module, not this
        # plugin's), so a plugin can import a sibling's tool without
        # silently registering it twice under this plugin.
        for attr_name, candidate in vars(tool_module).items():
            if attr_name.startswith("_"):
                continue
            if getattr(candidate, "__module__", None) != tool_module.__name__:
                continue
            if _is_strands_tool(candidate):
                tools.append(candidate)
    tools.sort(key=lambda t: t.tool_name)
    return tools


def tool_names(tools: list[Any] | None = None) -> list[str]:
    """Names of discovered tools (defaults to loading them)."""
    return [t.tool_name for t in (tools if tools is not None else load_tools())]
