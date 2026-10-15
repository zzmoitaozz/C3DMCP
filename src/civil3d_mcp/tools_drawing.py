"""
tools_drawing.py  –  Drawing info & object type MCP tools
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from fastmcp import FastMCP

from .client import Civil3DClient, Civil3DError

log = logging.getLogger("civil3d_mcp.tools.drawing")


def register(mcp: FastMCP, client: Civil3DClient, run_com: Callable) -> None:

    @mcp.tool(
        name="get_drawing_info",
        description=(
            "Get metadata for the active drawing (file, units, precision, CRS)."
        ),
    )
    async def get_drawing_info() -> dict[str, Any]:
        try:
            return await run_com(client.get_drawing_info)
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="list_civil_object_types",
        description=(
            "Count Civil 3D and model-space object types in the active drawing."
        ),
    )
    async def list_civil_object_types() -> dict[str, Any]:
        try:
            return await run_com(client.list_object_types)
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="analyze_drawing",
        description=(
            "Read-only inventory of model-space entities grouped by type and layer."
        ),
    )
    async def analyze_drawing() -> dict[str, Any]:
        try:
            return await run_com(client.analyze_drawing)
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="get_selected_objects_info",
        description=(
            "Get details for selected objects (handle, layer, type, name/description)."
        ),
    )
    async def get_selected_objects_info(max_count: int = 10) -> dict[str, Any]:
        try:
            objs = await run_com(client.get_selected_objects_info, max_count)
            return {"selected_objects": objs, "count": len(objs)}
        except Civil3DError as exc:
            return {"error": str(exc)}
