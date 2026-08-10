"""Tool listing endpoint — exposes the injected tool registry."""

from __future__ import annotations

import inspect

from fastapi import APIRouter, Depends

from ..deps import get_services, resolve_user

router = APIRouter(prefix="/v1/tools", tags=["tools"])


def _to_dict(tool) -> dict:
    if hasattr(tool, "to_dict"):
        return tool.to_dict()
    if isinstance(tool, dict):
        return tool
    return {"name": getattr(tool, "name", str(tool))}


@router.get("")
async def list_tools(user=Depends(resolve_user), services=Depends(get_services)) -> list[dict]:
    """List all registered tools as descriptors (empty when no registry)."""
    registry = services.tool_registry
    if registry is None:
        return []

    # ToolRegistry exposes list_all(); tolerate list_tools() for duck-typed registries.
    if hasattr(registry, "list_all"):
        result = registry.list_all()
        if inspect.isawaitable(result):
            result = await result
        return [_to_dict(t) for t in result]
    if hasattr(registry, "list_tools"):
        result = registry.list_tools()
        if inspect.isawaitable(result):
            result = await result
        return [_to_dict(t) for t in result]

    tools = getattr(registry, "tools", None)
    if isinstance(tools, dict):
        return [_to_dict(t) for t in tools.values()]
    if isinstance(tools, (list, tuple, set)):
        return [_to_dict(t) for t in tools]
    return []
