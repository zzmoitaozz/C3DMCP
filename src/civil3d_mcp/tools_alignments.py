"""
tools_alignments.py  –  Alignment MCP tools
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from fastmcp import FastMCP

from .client import Civil3DClient, Civil3DError

log = logging.getLogger("civil3d_mcp.tools.alignments")


def register(mcp: FastMCP, client: Civil3DClient, run_com: Callable) -> None:

    @mcp.tool(
        name="list_alignments",
        description=(
            "List alignments with name, style, length, and station range."
        ),
    )
    async def list_alignments() -> dict[str, Any]:
        try:
            aligns = await run_com(client.list_alignments)
            return {"alignments": aligns, "count": len(aligns)}
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="get_alignment_info",
        description=(
            "Get detailed geometry for one alignment, including entities."
        ),
    )
    async def get_alignment_info(alignment_name: str) -> dict[str, Any]:
        try:
            return await run_com(client.get_alignment_info, alignment_name)
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="get_station_offset",
        description=(
            "Compute station/offset of a world coordinate relative to an alignment."
        ),
    )
    async def get_station_offset(
        alignment_name: str,
        easting: float,
        northing: float,
    ) -> dict[str, Any]:
        try:
            return await run_com(
                client.get_station_offset,
                alignment_name, easting, northing,
            )
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="list_profiles",
        description=(
            "List vertical profiles attached to an alignment."
        ),
    )
    async def list_profiles(alignment_name: str) -> dict[str, Any]:
        try:
            profiles = await run_com(client.list_profiles, alignment_name)
            return {"alignment_name": alignment_name, "profiles": profiles, "count": len(profiles)}
        except Civil3DError as exc:
            return {"error": str(exc)}

    @mcp.tool(
        name="get_profile_info",
        description=(
            "Get detailed geometry and PVI data for one alignment profile."
        ),
    )
    async def get_profile_info(
        alignment_name: str,
        profile_name: str,
    ) -> dict[str, Any]:
        try:
            return await run_com(client.get_profile_info, alignment_name, profile_name)
        except Civil3DError as exc:
            return {"error": str(exc)}
