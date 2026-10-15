"""Generated Civil 3D API tools."""
from __future__ import annotations

import logging
import os
from typing import Any, Callable

from fastmcp import FastMCP

from . import api_discovery as disc
from .client import Civil3DClient, Civil3DError

log = logging.getLogger("civil3d_mcp.tools.api")

_MAX_LIST = 50


def _compact(obj: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in obj.items()
        if v is not None and not (isinstance(v, (str, list, dict)) and len(v) == 0)
    }


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
    legacy: bool | None = None,
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
        "Use c3d_api/list/get/invoke with a category.",
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

    by_key = {c.key: c for c in disc.CATEGORIES}

    def cat(key: str) -> disc.Category:
        if key not in by_key:
            raise Civil3DError(f"Unknown category '{key}'. Use one of: {', '.join(by_key)}.")
        return by_key[key]

    async def do_api(key: str, filter: str = "") -> dict[str, Any]:
        try:
            cat(key)
        except Civil3DError as exc:
            return {"error": str(exc)}
        needle = filter.lower()
        return _compact({
            "category": key,
            "interfaces": sorted(catalog.get(key, {})),
            **{k: [n for n in v if needle in n.lower()] for k, v in members[key].items()},
        })

    async def do_list(key: str, offset: int = 0, limit: int = _MAX_LIST) -> dict[str, Any]:
        offset, limit = max(0, offset), max(1, min(limit, 500))

        def work() -> tuple[int, list[str]]:
            objs = disc.collect_objects(doc(), cat(key))
            return len(objs), [disc.object_label(o) for o in objs[offset:offset + limit]]

        try:
            total, labels = await run_com(work)
        except Civil3DError as exc:
            return {"error": str(exc)}
        out: dict[str, Any] = {"category": key, "count": total, "objects": labels}
        if offset + limit < total:
            out["truncated"] = True
            out["next_offset"] = offset + limit
        return _compact(out)

    async def do_get(key: str, name: str, properties: list[str] | None = None) -> dict[str, Any]:
        try:
            category = cat(key)
        except Civil3DError as exc:
            return {"error": str(exc)}
        known = members[key]["properties"]
        wanted = [p for p in (properties or known) if p in known]

        def work() -> dict[str, Any]:
            return disc.read_properties(find(category, name), wanted)

        try:
            return _compact({"name": name, "properties": await run_com(work)})
        except Civil3DError as exc:
            return {"error": str(exc)}

    async def do_invoke(
        key: str, name: str, member: str, args: list[Any] | None = None, set_value: Any = None
    ) -> dict[str, Any]:
        try:
            category = cat(key)
        except Civil3DError as exc:
            return {"error": str(exc)}
        known = members[key]
        setting = set_value is not None
        if setting and member not in known["writable"]:
            return {"error": f"'{member}' is not a writable property of {key}."}
        if not setting and member not in known["methods"]:
            return {"error": f"'{member}' is not a method of {key}. See c3d_api."}

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

    keys = ", ".join(by_key)

    @mcp.tool(name="c3d_api", description=f"Discovered COM members of a category ({keys}); optional name filter.")
    async def c3d_api(category: str, filter: str = "") -> dict[str, Any]:
        return await do_api(category, filter)

    @mcp.tool(name="c3d_list", description="List objects of a category (paged: offset, limit).")
    async def c3d_list(category: str, offset: int = 0, limit: int = _MAX_LIST) -> dict[str, Any]:
        return await do_list(category, offset, limit)

    @mcp.tool(name="c3d_get", description="Read properties of one object by name/handle (empty properties = all).")
    async def c3d_get(category: str, name: str, properties: list[str] | None = None) -> dict[str, Any]:
        return await do_get(category, name, properties)

    @mcp.tool(
        name="c3d_invoke",
        description="Call a discovered method (args) or set a writable property (set_value). Destructive blocked.",
    )
    async def c3d_invoke(
        category: str, name: str, member: str, args: list[Any] | None = None, set_value: Any = None
    ) -> dict[str, Any]:
        return await do_invoke(category, name, member, args, set_value)

    count += 4

    if legacy is None:
        legacy = os.environ.get("CIVIL3D_LEGACY_TOOLS") == "1"
    if legacy:
        for key in by_key:
            def add(key: str = key) -> None:
                mcp.tool(name=f"c3d_{key}_api", description=f"API of {key}.")(
                    lambda filter="": do_api(key, filter))
                mcp.tool(name=f"c3d_{key}_list", description=f"List {key} objects.")(
                    lambda offset=0, limit=_MAX_LIST: do_list(key, offset, limit))
                mcp.tool(name=f"c3d_{key}_get", description=f"Read {key} properties.")(
                    lambda name, properties=None: do_get(key, name, properties))
                mcp.tool(name=f"c3d_{key}_invoke", description=f"Invoke {key} member.")(
                    lambda name, member, args=None, set_value=None: do_invoke(key, name, member, args, set_value))
            add()
            count += 4
    log.info("Registered %d generated Civil 3D API tools", count)
    return count