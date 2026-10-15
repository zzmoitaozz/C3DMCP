"""
api_discovery.py  -  Discover the Civil 3D COM API and classify it by object kind.

The Civil 3D type libraries (Aecc*) are scanned at runtime, every interface is
assigned to an object category (surfaces, alignments, pipe networks ...) and its
properties / methods are collected.  The resulting catalog drives the generated
per-category MCP tools in ``tools_api``.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

log = logging.getLogger("civil3d_mcp.api_discovery")


@dataclass(frozen=True)
class Category:
    key: str
    title: str
    patterns: tuple[str, ...]
    # Paths from the document to the objects. A path is a tuple of attribute
    # names; a collection in the middle is expanded (e.g. Sites -> Alignments).
    collections: tuple[tuple[str, ...], ...] = ()


# Order matters: the first matching category wins.
CATEGORIES: tuple[Category, ...] = (
    Category("pressure_networks", "Pressure networks (pipes, fittings, appurtenances)",
             (r"pressure", r"appurtenance", r"fitting"), (("PressurePipeNetworks",),)),
    Category("pipe_networks", "Gravity pipe networks (pipes, structures)",
             (r"pipe", r"structure", r"network"), (("PipeNetworks",),)),
    Category("surfaces", "Surfaces (TIN, grid, volume, snapshots)",
             (r"surface", r"^tin", r"grid", r"contour", r"breakline", r"boundar", r"watershed"),
             (("Surfaces",),)),
    Category("profiles", "Profiles and profile views",
             (r"profile", r"pvi", r"vertical"), (("AlignmentsSiteless", "Profiles"), ("Sites", "Alignments", "Profiles"))),
    Category("alignments", "Alignments and their entities",
             (r"alignment", r"station", r"superelev", r"offset"),
             (("AlignmentsSiteless",), ("Sites", "Alignments"))),
    Category("corridors", "Corridors, baselines, regions",
             (r"corridor", r"baseline", r"subassembl", r"assembl", r"codeset", r"daylight"),
             (("Corridors",), ("Assemblies",))),
    Category("sample_lines", "Sample lines, section views, material lists",
             (r"sample", r"section", r"material", r"quantity", r"takeoff"),
             (("AlignmentsSiteless", "SampleLineGroups"), ("Sites", "Alignments", "SampleLineGroups"))),
    Category("parcels", "Parcels and sites",
             (r"parcel", r"site", r"lot"), (("Sites",), ("Sites", "Parcels"))),
    Category("feature_lines", "Feature lines, grading, grading groups",
             (r"feature", r"grading", r"gradinggroup"), (("Sites", "FeatureLines"),)),
    Category("cogo_points", "COGO points, point groups, point files",
             (r"cogo", r"point", r"pointgroup", r"label"), (("Points",), ("PointGroups",))),
    Category("survey", "Survey databases, figures, networks",
             (r"survey", r"figure", r"traverse"), (("SurveyProjects",),)),
    Category("styles", "Styles, label styles, settings",
             (r"style", r"setting", r"table", r"code"), (("Styles",),)),
    Category("drawing", "Document, application, general objects",
             (r".*",), ()),
)

_BY_KEY = {c.key: c for c in CATEGORIES}

# Members never exposed by generated tools.
_BLOCKED_MEMBERS = frozenset({
    "delete", "erase", "purge", "save", "saveas", "close", "quit", "sendcommand",
    "postcommand", "eval", "open", "wblock", "exportdrawing",
})
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def classify_interface(name: str) -> str:
    """Map a COM interface / coclass name to a category key."""
    short = re.sub(r"^I?Aecc", "", name, flags=re.IGNORECASE).lower()
    short = re.sub(r"^i(?=[a-z]{3})", "", short) if name.startswith("I") and not short else short
    for category in CATEGORIES[:-1]:
        if any(re.search(p, short) for p in category.patterns):
            return category.key
    return "drawing"


@dataclass
class InterfaceInfo:
    name: str
    properties: list[str] = field(default_factory=list)
    writable: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)


def build_catalog(interfaces: Iterable[InterfaceInfo]) -> dict[str, dict[str, InterfaceInfo]]:
    """Group interfaces as {category_key: {interface_name: InterfaceInfo}}."""
    catalog: dict[str, dict[str, InterfaceInfo]] = {c.key: {} for c in CATEGORIES}
    for info in interfaces:
        catalog[classify_interface(info.name)][info.name] = info
    return catalog


def category_members(catalog: dict[str, dict[str, InterfaceInfo]], key: str) -> dict[str, list[str]]:
    """Merged, de-duplicated and safe member names for a category."""
    props: set[str] = set()
    writable: set[str] = set()
    methods: set[str] = set()
    for info in catalog.get(key, {}).values():
        props.update(info.properties)
        writable.update(info.writable)
        methods.update(info.methods)

    def safe(names: Iterable[str]) -> list[str]:
        return sorted(n for n in names if _IDENTIFIER.match(n) and n.lower() not in _BLOCKED_MEMBERS)

    return {"properties": safe(props), "writable": safe(writable), "methods": safe(methods)}


def get_category(key: str) -> Category:
    return _BY_KEY[key]


def scan_typelibs() -> list[InterfaceInfo]:
    """Read every Autodesk Civil 3D type library registered on this machine."""
    try:
        import pythoncom
        from win32com.client import selecttlb
    except ImportError:
        log.warning("pywin32 not available - API discovery skipped")
        return []

    result: dict[str, InterfaceInfo] = {}
    for spec in selecttlb.EnumTlbs():
        label = f"{spec.desc} {spec.dll or ''}"
        if not re.search(r"aecc|civil", label, re.IGNORECASE):
            continue
        try:
            lib = pythoncom.LoadTypeLib(spec.dll or spec.desc)
        except Exception as exc:  # noqa: BLE001
            log.debug("Skipping typelib %s: %s", spec.desc, exc)
            continue
        for index in range(lib.GetTypeInfoCount()):
            try:
                info = _read_typeinfo(lib.GetTypeInfo(index))
            except Exception as exc:  # noqa: BLE001
                log.debug("Skipping typeinfo %d in %s: %s", index, spec.desc, exc)
                continue
            if info is not None and info.name not in result:
                result[info.name] = info
    log.info("Discovered %d Civil 3D COM interfaces", len(result))
    return list(result.values())


def _read_typeinfo(typeinfo: Any) -> InterfaceInfo | None:
    import pythoncom

    attr = typeinfo.GetTypeAttr()
    if attr.typekind not in (pythoncom.TKIND_INTERFACE, pythoncom.TKIND_DISPATCH):
        return None
    name = typeinfo.GetDocumentation(-1)[0]
    if not re.match(r"^I?Aecc", name):
        return None
    info = InterfaceInfo(name=name)
    for i in range(attr.cFuncs):
        func = typeinfo.GetFuncDesc(i)
        member = typeinfo.GetNames(func.memid)[0]
        if member.startswith("_") or member in ("QueryInterface", "AddRef", "Release"):
            continue
        if func.invkind == pythoncom.INVOKE_PROPERTYGET:
            if member not in info.properties and not func.args:
                info.properties.append(member)
        elif func.invkind in (pythoncom.INVOKE_PROPERTYPUT, pythoncom.INVOKE_PROPERTYPUTREF):
            if member not in info.writable:
                info.writable.append(member)
        elif func.invkind == pythoncom.INVOKE_FUNC and member not in info.methods:
            info.methods.append(member)
    return info


# ---------------------------------------------------------------------------
# Object access helpers (pure Python over COM-like objects; unit-testable)
# ---------------------------------------------------------------------------

def iter_collection(col: Any) -> list[Any]:
    """Materialise a COM collection (Count/Item or iterable)."""
    if col is None:
        return []
    try:
        return list(col)
    except TypeError:
        pass
    items = []
    count = int(col.Count)
    for i in range(count):
        try:
            items.append(col.Item(i))
        except Exception:  # noqa: BLE001
            try:
                items.append(col.Item(i + 1))
            except Exception:  # noqa: BLE001
                continue
    return items


def collect_objects(doc: Any, category: Category) -> list[Any]:
    """Resolve all objects of a category from the Civil 3D document."""
    found: list[Any] = []
    seen: set[int] = set()
    for path in category.collections:
        level = [doc]
        for attr in path:
            nxt: list[Any] = []
            for parent in level:
                try:
                    nxt.extend(iter_collection(getattr(parent, attr)))
                except Exception as exc:  # noqa: BLE001
                    log.debug("Collection %s unavailable: %s", attr, exc)
            level = nxt
        for obj in level:
            ident = id(obj)
            if ident not in seen:
                seen.add(ident)
                found.append(obj)
    return found


def to_plain(value: Any, depth: int = 0) -> Any:
    """Convert COM values to JSON-safe data."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (tuple, list)):
        return [to_plain(v, depth + 1) for v in value[:200]]
    name = getattr(value, "Name", None)
    handle = getattr(value, "Handle", None)
    if isinstance(name, str) or isinstance(handle, str):
        return {"_object": getattr(value, "ObjectName", type(value).__name__), "name": name, "handle": handle}
    return str(value)


def read_properties(obj: Any, names: Iterable[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in names:
        try:
            out[name] = to_plain(getattr(obj, name))
        except Exception:  # noqa: BLE001
            continue
    return out


def object_label(obj: Any) -> str:
    for attr in ("Name", "Handle"):
        try:
            value = getattr(obj, attr)
            if value not in (None, ""):
                return str(value)
        except Exception:  # noqa: BLE001
            continue
    return ""
