"""
tools_surfaces.py  –  Surface MCP tools
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from fastmcp import FastMCP

from .client import Civil3DClient, Civil3DError

log = logging.getLogger("civil3d_mcp.tools.surfaces")


def register(mcp: FastMCP, client: Civil3DClient, run_com: Callable) -> None:

    @mcp.tool(
        name="list_surfaces",
        description=(
            "List TIN/Grid surfaces with basic statistics."
        ),
    )
    async def list_surfaces() -> dict[str, Any]:
        try:
            surfs = await run_com(client.list_surfaces)
            return {"surfaces": surfs, "count": len(surfs)}
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="get_surface_info",
        description=(
            "Get detailed stats for one named surface."
        ),
    )
    async def get_surface_info(surface_name: str) -> dict[str, Any]:
        try:
            return await run_com(client.get_surface_info, surface_name)
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="sample_surface_elevation",
        description=(
            "Sample surface elevation at one easting/northing coordinate."
        ),
    )
    async def sample_surface_elevation(
        surface_name: str,
        easting: float,
        northing: float,
    ) -> dict[str, Any]:
        try:
            return await run_com(
                client.sample_surface_elevation,
                surface_name, easting, northing,
            )
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="list_surface_definition",
        description=(
            "List definition items (boundaries, breaklines, files, points) for a surface."
        ),
    )
    async def list_surface_definition(surface_name: str) -> dict[str, Any]:
        try:
            return await run_com(client.list_surface_definition, surface_name)
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="tool_surface_mng",
        description=(
            "Tạo TIN surface từ COGO point theo mã điểm, rồi tạo profile surface cho các alignment."
        ),
    )
    async def tool_surface_mng(
        surface_name: str = "EG_Surface",
        profile_name: str = "EG_Profile",
        point_description: str = "EG",
    ) -> dict[str, Any]:
        try:
            return await run_com(
                client.create_surface_from_eg_cogo_points,
                surface_name,
                profile_name,
                point_description,
            )
        except Civil3DError as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Future tools to add:
    # - create_surface_from_dem
    # - create_surface_from_tin
    # - add_breakline_to_surface
    # - add_boundary_to_surface
    # - delete_surface
    # ------------------------------------------------------------------

    
