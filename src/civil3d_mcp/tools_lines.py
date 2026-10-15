"""
tools_lines.py  –  Line & Polyline MCP tools
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from fastmcp import FastMCP

from .client import Civil3DClient, Civil3DError

log = logging.getLogger("civil3d_mcp.tools.lines")


def register(mcp: FastMCP, client: Civil3DClient, run_com: Callable) -> None:

    @mcp.tool(
        name="create_line",
        description=(
            "Create a 3D line in model space from start/end XYZ."
        ),
    )
    async def create_line(
        x1: float,
        y1: float,
        z1: float,
        x2: float,
        y2: float,
        z2: float,
        layer: str = "0",
    ) -> dict[str, Any]:
        try:
            return await run_com(
                client.create_line,
                x1, y1, z1, x2, y2, z2, layer,
            )
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="create_polyline",
        description=(
            "Create a 3D polyline from ordered vertices [x,y,z] (or [x,y])."
        ),
    )
    async def create_polyline(
        vertices: list[list[float]],
        closed: bool = False,
        layer: str = "0",
    ) -> dict[str, Any]:
        if not vertices or len(vertices) < 2:
            return {"error": "At least 2 vertices are required."}

        # Accept [[x,y,z], ...] or [[x,y], ...] (z defaults to 0)
        verts_3d: list[tuple[float, float, float]] = []
        for v in vertices:
            if len(v) == 2:
                verts_3d.append((float(v[0]), float(v[1]), 0.0))
            elif len(v) >= 3:
                verts_3d.append((float(v[0]), float(v[1]), float(v[2])))
            else:
                return {"error": f"Invalid vertex: {v}. Expected [x, y] or [x, y, z]."}

        try:
            return await run_com(client.create_polyline, verts_3d, closed, layer)
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="list_lines",
        description=(
            "List line/polyline entities, optionally filtered by layer."
        ),
    )
    async def list_lines(layer_filter: str = "") -> dict[str, Any]:
        try:
            lines = await run_com(client.list_lines, layer_filter)
            return {"lines": lines, "count": len(lines)}
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="create_solid_box",
        description=(
            "Create an axis-aligned 3D box solid from origin and dimensions."
        ),
    )
    async def create_solid_box(
        x: float,
        y: float,
        z: float,
        length: float,
        width: float,
        height: float,
        layer: str = "0",
    ) -> dict[str, Any]:
        """Create a 3D box from its origin and dimensions."""
        try:
            return await run_com(
                client.create_solid_box, x, y, z, length, width, height, layer
            )
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="create_solid_primitive",
        description=(
            "Create a detailed 3D solid: cylinder (optional inner_radius for tubes/washers), "
            "cone (optional top_radius for frustum), sphere, torus, extrude (polygon points [[x,y],...] "
            "+ height + taper), or revolve (profile points [[radius,height],...] swept around the axis). "
            "Anchored at (x,y,z) at the base (sphere/torus: centre); 'direction' tilts the local Z axis. "
            "Use revolve for insulator discs, shackles, nuts and other round parts."
        ),
    )
    async def create_solid_primitive(
        kind: str,
        x: float,
        y: float,
        z: float,
        direction: list[float] | None = None,
        radius: float = 0.0,
        height: float = 0.0,
        inner_radius: float = 0.0,
        top_radius: float = 0.0,
        tube_radius: float = 0.0,
        points: list[list[float]] | None = None,
        taper_angle_deg: float = 0.0,
        sweep_angle_deg: float = 360.0,
        layer: str = "0",
    ) -> dict[str, Any]:
        try:
            d = tuple(direction) if direction else (0.0, 0.0, 1.0)
            if len(d) != 3:
                return {"error": "direction must have three components."}
            return await run_com(
                client.create_solid_primitive, kind, x, y, z, d, radius, height,
                inner_radius, top_radius, tube_radius, points, taper_angle_deg,
                sweep_angle_deg, layer,
            )
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="create_checkpoint",
        description=(
            "Create an undo checkpoint for later restore in this server session."
        ),
    )
    async def create_checkpoint() -> dict[str, Any]:
        """Create a restore point in the active drawing's undo history."""
        try:
            return await run_com(client.create_checkpoint)
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="restore_checkpoint",
        description=(
            "Undo drawing changes back to the latest checkpoint in this server session."
        ),
    )
    async def restore_checkpoint(checkpoint_id: str) -> dict[str, Any]:
        """Restore the active drawing to a checkpoint created by this server."""
        try:
            return await run_com(client.restore_checkpoint, checkpoint_id)
        except Civil3DError as exc:
            return {"error": str(exc)}
