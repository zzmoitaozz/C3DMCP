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
            "Create a new COGO (Coordinate Geometry) point in the active Civil 3D "
            "drawing. Provide easting/northing or X/Y, optional elevation or Z, "
            "and description. "
            "Returns the assigned point number and coordinates."
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
        """
        Parameters
        ----------
        easting, northing : float, optional
            Coordinates in drawing units. Alternatively provide X and Y.
        elevation : float, optional
            Elevation in drawing units (default 0.0). Alternatively provide Z.
        description : str
            Point description / raw description text.
        """
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
            "List COGO points in the active Civil 3D drawing. "
            "Returns point number, northing, easting, elevation and description "
            "for up to max_count points."
        ),
    )
    async def list_cogo_points(max_count: int = 50) -> dict[str, Any]:
        """
        Parameters
        ----------
        max_count : int
            Maximum number of points to return (default 50).
        """
        try:
            pts = await run_com(client.list_cogo_points, max_count)
            return {"cogo_points": pts, "count": len(pts)}
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="delete_cogo_point",
        description=(
            "Delete a COGO point from the active drawing by its point number. "
            "Returns success confirmation or an error if the point is not found."
        ),
    )
    async def delete_cogo_point(point_number: int) -> dict[str, Any]:
        """
        Parameters
        ----------
        point_number : int
            The Civil 3D point number to delete.
        """
        try:
            await run_com(client.delete_cogo_point, point_number)
            return {"success": True, "deleted_point_number": point_number}
        except Civil3DError as exc:
            return {"error": str(exc)}
