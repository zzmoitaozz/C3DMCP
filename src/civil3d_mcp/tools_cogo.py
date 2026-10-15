"""
tools_cogo.py  –  COGO Point MCP tools
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from fastmcp import FastMCP

from .client import Civil3DClient, Civil3DError

log = logging.getLogger("civil3d_mcp.tools.cogo")


def register(mcp: FastMCP, client: Civil3DClient, run_com: Callable) -> None:

    @mcp.tool(
        name="create_cogo_point",
        description=(
            "Create a COGO point from easting/northing (or X/Y), optional elevation/Z, "
            "and description. Returns point number and coordinates."
        ),
    )
    async def create_cogo_point(
        easting: float | None = None,
        northing: float | None = None,
        elevation: float | None = None,
        description: str = "",
        X: float | None = None,
        Y: float | None = None,
        Z: float | None = None,
    ) -> dict[str, Any]:
        try:
            if easting is not None and X is not None:
                raise Civil3DError("Provide either easting or X, not both.")
            if northing is not None and Y is not None:
                raise Civil3DError("Provide either northing or Y, not both.")
            if elevation is not None and Z is not None:
                raise Civil3DError("Provide either elevation or Z, not both.")
            resolved_easting = easting if easting is not None else X
            resolved_northing = northing if northing is not None else Y
            if resolved_easting is None or resolved_northing is None:
                raise Civil3DError(
                    "Provide easting and northing, or their X and Y aliases."
                )
            return await run_com(
                client.create_cogo_point,
                northing=resolved_northing,
                easting=resolved_easting,
                elevation=elevation if elevation is not None else (Z or 0.0),
                description=description,
            )
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="list_cogo_points",
        description=(
            "List COGO points (number, coordinates, elevation, description)."
        ),
    )
    async def list_cogo_points(max_count: int = 50) -> dict[str, Any]:
        try:
            pts = await run_com(client.list_cogo_points, max_count)
            return {"cogo_points": pts, "count": len(pts)}
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="delete_cogo_point",
        description=(
            "Delete a COGO point by point number."
        ),
    )
    async def delete_cogo_point(point_number: int) -> dict[str, Any]:
        try:
            await run_com(client.delete_cogo_point, point_number)
            return {"success": True, "deleted_point_number": point_number}
        except Civil3DError as exc:
            return {"error": str(exc)}
