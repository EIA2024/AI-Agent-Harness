"""Owner-aware tool visibility (P1-014).

The broker's ``list_available_tools`` and the API ``GET /v1/tools`` both feed a
tool listing; neither consulted per-owner capability. This service is the single
seam that filters tools by owner — today it applies an injected per-owner deny
set (and an optional risk ceiling); richer per-owner grants plug in here.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


class CapabilityService:
    """Decides which tools an owner may see / be offered."""

    def __init__(
        self,
        tool_registry: Any,
        *,
        owner_deny: dict[str, set[str]] | None = None,
        risk_ceiling: int = 4,
    ) -> None:
        self.tool_registry = tool_registry
        # owner_id (str) -> set of tool names the owner must never see
        self.owner_deny = {str(k): set(v) for k, v in (owner_deny or {}).items()}
        self.risk_ceiling = risk_ceiling

    def visible_tools(
        self, owner_id: Any, *, query: str | None = None
    ) -> list[Any]:
        """Return the descriptors visible to ``owner_id``."""
        deny = self.owner_deny.get(str(owner_id), set())
        registry = self.tool_registry
        if hasattr(registry, "list_all"):
            tools = registry.list_all()
            if hasattr(tools, "__await__"):
                raise TypeError("list_all must be sync; awaiting unsupported here")
        else:
            tools = registry.list_tools() if hasattr(registry, "list_tools") else []
            if hasattr(tools, "__await__"):
                raise TypeError("list_tools must be sync; awaiting unsupported here")
        out = []
        for tool in tools:
            name = tool.name if hasattr(tool, "name") else str(tool)
            risk = getattr(tool, "risk_level", 0) or 0
            if name in deny:
                continue
            if risk > self.risk_ceiling:
                continue
            if query and not self._matches(query, tool, name):
                continue
            out.append(tool)
        return out

    def can_use(self, owner_id: Any, tool_name: str) -> bool:
        if tool_name in self.owner_deny.get(str(owner_id), set()):
            return False
        getter = getattr(self.tool_registry, "get", None)
        tool = getter(tool_name) if getter is not None else None
        if tool is None:
            return False
        return (getattr(tool, "risk_level", 0) or 0) <= self.risk_ceiling

    def _matches(self, query: str, tool: Any, name: str) -> bool:
        q = query.lower()
        if q in name.lower():
            return True
        description = getattr(tool, "description", "") or ""
        return q in description.lower()


def iter_tools(registry: Any) -> Iterable[Any]:
    """Best-effort iteration over a registry's descriptors."""
    if hasattr(registry, "list_all"):
        tools = registry.list_all()
    elif hasattr(registry, "list_tools"):
        tools = registry.list_tools()
    else:
        tools = getattr(registry, "tools", [])
    if hasattr(tools, "__await__"):
        raise TypeError("registry listing must be sync for capability filtering")
    return tools
