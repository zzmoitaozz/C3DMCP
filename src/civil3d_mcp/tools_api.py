"""
tools_api.py  -  Auto-generated MCP tools, one set per Civil 3D object category.

For every category found by ``api_discovery`` (surfaces, alignments, ...):
  c3d_<cat>_api     list the discovered properties / methods
  c3d_<cat>_list    list the objects of that kind in the active drawing
  c3d_<cat>_get     read properties of one object
  c3d_<cat>_invoke  call a discovered method or set a discovered property
plus ``c3d_api_categories`` which lists all categories.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from fastmcp import FastMCP

from . import api_discovery as disc
from .client import Civil3DClient, Civil3DError

log = logging.getLogger("civil3d_mcp.tools.api")

_MAX_LIST = 200


def build_catalog() -> dict[str, dict[str, disc.InterfaceInfo]]:
    try:
        return disc.build_catalog(disc.scan_typelibs())
    except Exception as exc:  # noqa: BLE001
        log.warning("API discovery failed: %s", exc)
        return disc.build_catalog([])


def register(
    mcp: FastMCP,
    client: Civil3DClient,
    run_com: Callable,
    catalog: dict[str, dict[str, disc.InterfaceInfo]] | None = None,
) -> int:
    """Register the generated tools and return how many were added."""
    catalog = catalog if catalog is not None else build_catalog()
    members = {c.key: disc.category_members(catalog, c.key) for c in disc.CATEGORIES}
    count = 0

    def doc() -> Any:
        client._ensure_connected()
        return client._civil_doc()

    def find(category: disc.Category, name: str) -> Any:
        for obj in disc.collect_objects(doc(), category):
            if disc.object_label(obj) == name:
                return obj
        raise Civil3DError(f"No {category.key} object named '{name}'.")

    @mcp.tool(
        name="c3d_api_categories",
        description="List the Civil 3D object categories with discovered API sizes. "
        "Each category has c3d_<category>_api/list/get/invoke tools.",
    )
    async def c3d_api_categories() -> dict[str, Any]:
        return {
            "categories": [
                {
                    "category": c.key,
                    "title": c.title,
                    "interfaces": len(catalog.get(c.key, {})),
                    "properties": len(members[c.key]["properties"]),
                    "methods": len(members[c.key]["methods"]),
                }
                for c in disc.CATEGORIES
            ]
        }

    count += 1

    def make_tools(category: disc.Category) -> int:
        key = category.key
        known = members[key]

        @mcp.tool(
            name=f"c3d_{key}_api",
            description=f"Discovered Civil 3D COM API for: {category.title}. "
            "Optionally filter member names by substring.",
        )
        async def api(filter: str = "") -> dict[str, Any]:
            needle = filter.lower()
            return {
                "category": key,
                "interfaces": sorted(catalog.get(key, {})),
                **{
                    kind: [n for n in names if needle in n.lower()]
                    for kind, names in known.items()
                },
            }

        @mcp.tool(
            name=f"c3d_{key}_list",
            description=f"List {category.title} objects in the active drawing "
            f"(name/handle, up to {_MAX_LIST}).",
        )
        async def list_objects() -> dict[str, Any]:
            def work() -> tuple[int, list[str]]:
                objs = disc.collect_objects(doc(), category)
                return len(objs), [disc.object_label(o) for o in objs[:_MAX_LIST]]

            try:
                total, labels = await run_com(work)
                return {"category": key, "count": total, "objects": labels}
            except Civil3DError as exc:
                return {"error": str(exc)}

        @mcp.tool(
            name=f"c3d_{key}_get",
            description=f"Read properties of one {category.title} object by name or handle. "
            "Pass property names, or leave empty for all discovered scalar properties.",
        )
        async def get_object(name: str, properties: list[str] | None = None) -> dict[str, Any]:
            wanted = [p for p in (properties or known["properties"]) if p in known["properties"]]

            def work() -> dict[str, Any]:
                return disc.read_properties(find(category, name), wanted)

            try:
                return {"name": name, "properties": await run_com(work)}
            except Civil3DError as exc:
                return {"error": str(exc)}

        @mcp.tool(
            name=f"c3d_{key}_invoke",
            description=f"Call a discovered method (args) or set a discovered writable property "
            f"(set_value) on one {category.title} object. Destructive members are blocked.",
        )
        async def invoke(
            name: str,
            member: str,
            args: list[Any] | None = None,
            set_value: Any = None,
        ) -> dict[str, Any]:
            setting = set_value is not None
            if setting and member not in known["writable"]:
                return {"error": f"'{member}' is not a writable property of {key}."}
            if not setting and member not in known["methods"]:
                return {"error": f"'{member}' is not a method of {key}. See c3d_{key}_api."}

            def work() -> Any:
                obj = find(category, name)
                if setting:
                    setattr(obj, member, set_value)
                    return True
                return disc.to_plain(getattr(obj, member)(*(args or [])))

            try:
                return {"result": await run_com(work)}
            except Civil3DError as exc:
                return {"error": str(exc)}
            except Exception as exc:  # noqa: BLE001
                return {"error": f"{member} failed: {exc}"}

        return 4

    for category in disc.CATEGORIES:
        count += make_tools(category)
    log.info("Registered %d generated Civil 3D API tools", count)
    return count
