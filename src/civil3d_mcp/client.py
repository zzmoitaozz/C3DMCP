"""
client.py  –  Civil3DClient
============================
Wraps COM automation to the running Civil 3D instance.

Connection strategy
-------------------
1.  win32com.client.GetActiveObject(prog_id)
    Grabs the live acad.exe COM server.  We try Civil 3D's own ProgID first
    ("AeccXUiLand.AeccApplication.14.0") so the full Civil 3D object model
    (CogoPoints, Surfaces, Alignments …) is available.  Fall back to the
    plain AutoCAD ProgID when only basic drawing tools are needed.

2.  pythonnet (clr) – optional managed-assembly bridge
    Loads AeccDbMgd.dll / AeccLandMgd.dll so Python can call Civil 3D .NET
    APIs that are NOT exposed through the raw IDispatch COM interface.

Civil 3D must be running and have a drawing open.
Every public method returns plain Python dicts / lists so the MCP tool layer
never touches COM objects directly.
"""

from __future__ import annotations

import logging
import math
import os
import sys
import time
import uuid
from typing import Any

log = logging.getLogger("civil3d_mcp.client")

# ---------------------------------------------------------------------------
# Windows-only import guards
# ---------------------------------------------------------------------------
try:
    import pythoncom
    import win32com.client as w32
    _WIN32 = True
except ImportError:
    _WIN32 = False
    log.warning("pywin32 not found – COM automation unavailable")

try:
    import clr  # pythonnet
    _CLR = True
except ImportError:
    _CLR = False
    log.warning("pythonnet not found – managed assembly access unavailable")


# ---------------------------------------------------------------------------
# Known Autodesk assembly locations (Civil 3D 2023 – 2026 defaults)
# ---------------------------------------------------------------------------
# Required DLL — always present in Civil 3D installations
_CIVIL3D_DLL_NAMES = [
    "AeccDbMgd.dll",
    "AeccPressurePipesMgd.dll",
]
# Optional DLL — present in Civil 3D 2023/2024 but removed in 2025+
_CIVIL3D_DLL_NAMES_OPTIONAL = ["AeccLandMgd.dll"]
_ACAD_DLL_NAMES = ["acdbmgd.dll", "acmgd.dll",
    "AcCoreMgd.dll"]
_AUTODESK_ROOTS = [
    r"C:\Program Files\Autodesk\AutoCAD 2026\C3D",
    r"C:\Program Files\Autodesk\AutoCAD 2026",
    r"C:\Program Files\Autodesk\AutoCAD 2025\C3D",
    r"C:\Program Files\Autodesk\AutoCAD 2025",
    r"C:\Program Files\Autodesk\AutoCAD 2024\C3D",
    r"C:\Program Files\Autodesk\AutoCAD 2024",
    r"C:\Program Files\Autodesk\AutoCAD 2023\C3D",
    r"C:\Program Files\Autodesk\AutoCAD 2023",
]
# Override the entire bin folder via env var
_CIVIL3D_BIN = os.getenv("CIVIL3D_BIN_PATH", "").strip()


def _find_dll(name: str) -> str | None:
    """Return the first existing path for a named Autodesk DLL."""
    roots: list[str] = []
    if _CIVIL3D_BIN:
        roots.append(_CIVIL3D_BIN)
        # Also search the parent folder (e.g. AutoCAD 2025\ when BIN_PATH is AutoCAD 2025\C3D)
        parent = os.path.dirname(_CIVIL3D_BIN)
        if parent and parent != _CIVIL3D_BIN:
            roots.append(parent)
    roots.extend(_AUTODESK_ROOTS)
    for root in roots:
        path = os.path.join(root, name)
        if os.path.exists(path):
            return path
    return None


# ---------------------------------------------------------------------------
# Civil3DClient
# ---------------------------------------------------------------------------

class _CivilDocuments:
    """Resolve attributes across the Land, Roadway, Pipe and Survey documents."""

    def __init__(self, primary: Any, extras: list[Any]) -> None:
        self._docs = [primary, *extras]

    def __getattr__(self, name: str) -> Any:
        error: Exception | None = None
        for doc in self._docs:
            try:
                return getattr(doc, name)
            except Exception as exc:
                error = error or exc
        raise AttributeError(name) from error


class Civil3DError(RuntimeError):
    """Raised for any Civil 3D COM / API error."""


class Civil3DClient:
    """
    Thin wrapper around the Civil 3D COM object model.

    Usage
    -----
        client = Civil3DClient()
        client.connect()               # once at server startup
        info = client.get_drawing_info()
        client.disconnect()            # on shutdown
    """

    def __init__(self) -> None:
        self._acad: Any = None       # AcadApplication COM object (AutoCAD base)
        self._civil: Any = None      # AeccApplication COM object (Civil 3D layer)
        self._doc: Any = None        # AeccDocument (Civil 3D) or AcadDocument fallback
        self._connected = False
        self._checkpoint_id: str | None = None
        self._checkpoint_document: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    # Civil 3D ProgIDs in order of preference.
    _CIVIL3D_PROG_IDS = [
        "AeccXUiLand.AeccApplication.13.8",  # Civil 3D 2026 (installed type library)
        "AeccXUiLand.AeccApplication.14.4",  # Civil 3D 2026
        "AeccXUiLand.AeccApplication.13.7",  # Civil 3D 2025
        "AeccXUiLand.AeccApplication.14.0",  # Civil 3D 2024
        "AeccXUiLand.AeccApplication.13.0",  # Civil 3D 2023
    ]

    _CIVIL3D_EXTRA_PROG_IDS = [
        "AeccXUiRoadway.AeccRoadwayApplication.13.8",
        "AeccXUiPipe.AeccPipeApplication.13.8",
        "AeccXUiSurvey.AeccSurveyApplication.13.8",
    ]

    def connect(self) -> None:
        """Attach to the running Civil 3D / AutoCAD instance via COM.

        Strategy
        --------
        1. Connect to AutoCAD (always registers as 'AutoCAD.Application').
        2. Promote to Civil 3D by calling acad.GetInterfaceObject() with known
           Civil 3D ProgIDs.  This works even when Civil 3D hasn't registered
           its AeccApplication in the Running Object Table (Civil 3D 2025).
        3. Fall back to the AutoCAD document only — Civil 3D collections won't
           be available but basic drawing info will work.
        """
        if not _WIN32:
            raise Civil3DError(
                "pywin32 is required for COM automation. "
                "Install with: pip install pywin32"
            )

        if sys.maxsize <= 2**31 - 1:
            raise Civil3DError(
                "Civil 3D requires a 64-bit Python interpreter. "
                "Start the server with a 64-bit Python 3.11/3.12 interpreter, "
                "for example: py -3.11-64 -m civil3d_mcp.server"
            )

        log.info("Connecting to Civil 3D via COM…")

        # Step 1 — connect to AutoCAD base application.
        try:
            self._acad = self._find_visible_acad() or w32.GetActiveObject("AutoCAD.Application")
            log.info("AutoCAD.Application connected – drawing: %s",
                     self._acad.ActiveDocument.Name)
        except Exception as exc:
            hresult = getattr(exc, "hresult", None)
            detail = f"{exc}"
            if hresult is not None:
                detail = f"{detail} (HRESULT 0x{hresult & 0xFFFFFFFF:08X})"
            raise Civil3DError(
                "Could not connect to AutoCAD/Civil 3D. "
                "Make sure Civil 3D is open with a drawing loaded. "
                f"Underlying COM error: {detail}"
            ) from exc

        # Step 2 — promote to Civil 3D via GetInterfaceObject.
        # This asks the already-running AutoCAD/C3D process to return its
        # Civil 3D application interface — works even when the Aecc ProgID isn't
        # in the ROT (Civil 3D 2025 behaviour).
        self._civil = None
        for prog_id in self._CIVIL3D_PROG_IDS:
            try:
                civil = self._acad.GetInterfaceObject(prog_id)
                if civil is not None:
                    self._civil = civil
                    self._doc = civil.ActiveDocument
                    log.info("Civil 3D interface acquired via GetInterfaceObject(%s)", prog_id)
                    break
            except Exception:
                continue

        # Corridors live on the Roadway application and pipe networks on the
        # Pipe application; the Land document does not expose them.
        self._civil_extra = []
        if self._civil is not None:
            for prog_id in self._CIVIL3D_EXTRA_PROG_IDS:
                try:
                    self._civil_extra.append(self._acad.GetInterfaceObject(prog_id))
                except Exception as exc:
                    log.debug("Optional Civil 3D interface %s unavailable: %s", prog_id, exc)

        if self._civil is None:
            # Fallback: plain AutoCAD document — Civil 3D collections unavailable.
            self._doc = self._acad.ActiveDocument
            log.warning(
                "Could not acquire Civil 3D interface. "
                "Connected as plain AutoCAD — Civil 3D collections (surfaces, "
                "alignments, COGO points) will not be available. "
                "Ensure Civil 3D is launched (not just AutoCAD)."
            )

        if self._doc is None:
            raise Civil3DError("No active drawing found.")

        self._load_managed_assemblies()
        self._connected = True
        log.info("Civil 3D client ready – drawing: %s", self._doc.Name)

    @staticmethod
    def _find_visible_acad() -> Any:
        """Prefer the interactive AutoCAD/Civil 3D instance the user can see.

        GetActiveObject returns whichever instance is first in the Running
        Object Table, which may be a hidden background instance.
        """
        try:
            rot = pythoncom.GetRunningObjectTable()
            ctx = pythoncom.CreateBindCtx(0)
            for moniker in rot.EnumRunning():
                try:
                    if not moniker.GetDisplayName(ctx, None).startswith("!AutoCAD.Application"):
                        continue
                    app = w32.Dispatch(rot.GetObject(moniker).QueryInterface(pythoncom.IID_IDispatch))
                    if app.Visible and app.Documents.Count > 0:
                        return app
                except Exception:
                    continue
        except Exception as exc:
            log.debug("Running Object Table scan failed: %s", exc)
        return None

    def _load_managed_assemblies(self) -> None:
        """Optionally load Autodesk .NET DLLs via pythonnet.

        No tool depends on managed types: every operation goes through COM.
        Civil 3D 2025+ runs on .NET 8 while pythonnet here hosts the .NET
        Framework runtime, so loading fails with BadImageFormatException.
        The bridge is therefore opt-in (CIVIL3D_LOAD_MANAGED=1) and failures
        are logged once at debug level instead of as warnings per DLL.
        """
        if os.getenv("CIVIL3D_LOAD_MANAGED", "").strip().lower() not in ("1", "true", "yes"):
            log.debug("Managed assembly loading disabled; using COM only.")
            return
        if not _CLR:
            log.warning("pythonnet not available; using COM only.")
            return

        for dll in _ACAD_DLL_NAMES + _CIVIL3D_DLL_NAMES + _CIVIL3D_DLL_NAMES_OPTIONAL:
            path = _find_dll(dll)
            if not path:
                log.debug("Assembly not present (skipping): %s", dll)
                continue
            try:
                clr.AddReference(path)
                log.debug("Loaded assembly: %s", path)
            except Exception as exc:
                log.warning(
                    "Managed bridge unavailable (%s: %s); continuing with COM only.",
                    type(exc).__name__, str(exc).splitlines()[0] if str(exc) else "",
                )
                return
    def disconnect(self) -> None:
        self._acad = None
        self._civil = None
        self._doc = None
        self._connected = False
        log.info("Civil 3D client disconnected")

    def _ensure_connected(self) -> None:
        if not self._connected or self._acad is None:
            # The server may have started before Civil 3D; retry once per call.
            try:
                self.connect()
            except Civil3DError as exc:
                raise Civil3DError(
                    "Not connected to Civil 3D. Open Civil 3D with a drawing "
                    f"loaded and retry. Detail: {exc}"
                ) from exc
            return
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                # Refresh active document from Civil 3D layer if available, else AutoCAD
                source = self._civil if self._civil is not None else self._acad
                self._doc = source.ActiveDocument
                return
            except Exception as exc:
                last_exc = exc
                # RPC_E_CALL_REJECTED / SERVERCALL_RETRYLATER: AutoCAD is busy.
                if getattr(exc, "hresult", None) in (-2147418111, -2147417846):
                    time.sleep(0.3 * (attempt + 1))
                    continue
                break
        # COM reference is stale (e.g. Civil 3D restarted or drawing switched): rebind.
        self._connected = False
        try:
            self.connect()
        except Civil3DError as exc:
            raise Civil3DError(
                f"Lost connection to Civil 3D: {last_exc}; reconnect failed: {exc}"
            ) from exc

    @staticmethod
    def _wait_idle(acad_doc: Any, timeout: float = 5.0) -> None:
        """Wait until AutoCAD has finished queued SendCommand work."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if int(acad_doc.GetVariable("CMDACTIVE")) == 0:
                    return
            except Exception:
                return
            time.sleep(0.1)

    @staticmethod
    def _ensure_layer(acad_doc: Any, layer: str) -> None:
        """Create the layer when missing; assigning an unknown layer raises 'Key not found'."""
        layers = acad_doc.Layers
        try:
            layers.Item(layer)
        except Exception:
            layers.Add(layer)

    @staticmethod
    def _entity_id(entity: Any) -> str:
        try:
            return str(entity.ObjectID)
        except Exception:
            return str(getattr(entity, "Handle", ""))

    @staticmethod
    def _discard(entity: Any) -> None:
        try:
            entity.Delete()
        except Exception:
            log.warning("Could not remove partially created entity.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pt3d(x: float, y: float, z: float) -> Any:
        """Build a VT_ARRAY|VT_R8 VARIANT for a 3-D point."""
        return w32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [x, y, z])

    @staticmethod
    def _out_double() -> Any:
        """Build a by-reference VT_R8 VARIANT for COM out-parameters."""
        return w32.VARIANT(pythoncom.VT_R8 | pythoncom.VT_BYREF, 0.0)

    # ------------------------------------------------------------------
    # Drawing info
    # ------------------------------------------------------------------

    def get_drawing_info(self) -> dict[str, Any]:
        """Return metadata about the active drawing."""
        self._ensure_connected()
        doc = self._doc
        try:
            return {
                "name": str(doc.Name),
                "full_path": str(doc.FullName),
                "saved": bool(doc.Saved),
                "insertion_units": str(doc.GetVariable("INSUNITS")),
                "linear_units": str(doc.GetVariable("LUNITS")),
                "angular_units": str(doc.GetVariable("AUNITS")),
                "precision": str(doc.GetVariable("LUPREC")),
                "coordinate_system": str(
                    getattr(doc, "CoordinateSystemCode", "not set")
                ),
            }
        except Exception as exc:
            raise Civil3DError(f"get_drawing_info failed: {exc}") from exc

    def list_object_types(self) -> dict[str, Any]:
        """
        Count Civil 3D objects by type.
        Returns two dicts:
          'model_space_counts'  – entity counts from model space iteration
          'civil3d_collections' – counts from named Civil 3D collections
        """
        self._ensure_connected()
        # --- model space entity tally ---
        model_counts: dict[str, int] = {}
        try:
            for obj in self._doc.ModelSpace:
                name = getattr(obj, "ObjectName", "Unknown")
                model_counts[name] = model_counts.get(name, 0) + 1
        except Exception as exc:
            log.warning("Model space iteration partial: %s", exc)

        # --- Civil 3D named collections ---
        civil_counts: dict[str, int] = {}
        collection_attrs = {
            "Points": "cogo_points",
            "AlignmentsSiteless": "alignments",
            "Surfaces": "surfaces",
            "Corridors": "corridors",
            "Sites": "sites",
            "Profiles": "profiles",
            "PipeNetworks": "pipe_networks",
        }
        for attr, label in collection_attrs.items():
            try:
                col = getattr(self._civil_doc(), attr, None)
                if col is not None:
                    civil_counts[label] = int(col.Count)
            except Exception:
                pass

        return {
            "model_space_counts": model_counts,
            "civil3d_collections": civil_counts,
            "total_model_objects": sum(model_counts.values()),
        }

    def analyze_drawing(self) -> dict[str, Any]:
        """Return a read-only model-space inventory grouped by layer and type."""
        self._ensure_connected()
        entities_by_type: dict[str, int] = {}
        layers: dict[str, dict[str, Any]] = {}
        try:
            for entity in self._doc.ModelSpace:
                object_name = str(entity.ObjectName)
                layer_name = str(entity.Layer)
                if not object_name:
                    raise Civil3DError("A model-space entity has no object type.")

                entities_by_type[object_name] = (
                    entities_by_type.get(object_name, 0) + 1
                )
                layer_key = layer_name.casefold()
                layer = layers.setdefault(
                    layer_key,
                    {"name": layer_name, "count": 0, "entities_by_type": {}},
                )
                layer["count"] += 1
                layer_types = layer["entities_by_type"]
                layer_types[object_name] = layer_types.get(object_name, 0) + 1
        except Civil3DError:
            raise
        except Exception as exc:
            raise Civil3DError(f"analyze_drawing failed: {exc}") from exc

        return {
            "total_model_space_entities": sum(entities_by_type.values()),
            "entities_by_type": dict(sorted(entities_by_type.items())),
            "layers": [
                {
                    **layer,
                    "entities_by_type": dict(
                        sorted(layer["entities_by_type"].items())
                    ),
                }
                for layer in sorted(
                    layers.values(), key=lambda item: item["name"].casefold()
                )
            ],
        }

    def get_selected_objects_info(self, max_count: int = 10) -> list[dict[str, Any]]:
        """Return properties of currently selected Civil 3D objects."""
        self._ensure_connected()
        results: list[dict[str, Any]] = []
        try:
            # PickfirstSelectionSet is the live current selection (grips/pickfirst)
            active_ss = getattr(self._doc, "PickfirstSelectionSet", None)
            if active_ss is None or active_ss.Count == 0:
                # Fall back: find the PICKFIRST named set in SelectionSets
                active_ss = None
                ss = self._doc.SelectionSets
                for i in range(ss.Count):
                    s = ss.Item(i)
                    if s.Name.upper() == "PICKFIRST" and s.Count > 0:
                        active_ss = s
                        break
            if active_ss is None or active_ss.Count == 0:
                return []
            for i, raw in enumerate(active_ss):
                if i >= max_count:
                    break
                object_name = getattr(raw, "ObjectName", "Unknown")
                # Re-dispatch to the concrete COM interface so type-specific
                # attributes (StartPoint, Coordinates, Length, etc.) are available.
                obj = w32.Dispatch(raw)
                info: dict[str, Any] = {
                    "index": i,
                    "object_name": object_name,
                    "handle": str(getattr(obj, "Handle", "N/A")),
                    "layer": str(getattr(obj, "Layer", "N/A")),
                    "object_id": str(getattr(obj, "ObjectID", "N/A")),
                }
                # Named Civil 3D / AutoCAD attributes
                for attr in ("Name", "Description", "StyleName"):
                    val = getattr(obj, attr, None)
                    if val is not None:
                        info[attr.lower()] = str(val)
                # Length (lines, polylines, alignments, …)
                try:
                    info["length"] = float(obj.Length)
                except Exception:
                    pass
                # Geometry by entity type
                if object_name == "AcDbLine":
                    try:
                        sp, ep = obj.StartPoint, obj.EndPoint
                        info["start"] = {"x": float(sp[0]), "y": float(sp[1]), "z": float(sp[2])}
                        info["end"]   = {"x": float(ep[0]), "y": float(ep[1]), "z": float(ep[2])}
                    except Exception:
                        pass
                elif object_name in ("AcDb2dPolyline", "AcDb3dPolyline"):
                    try:
                        coords = obj.Coordinates
                        verts = [
                            {"x": float(coords[j]), "y": float(coords[j + 1]), "z": float(coords[j + 2])}
                            for j in range(0, len(coords) - 2, 3)
                        ]
                        info["vertex_count"] = len(verts)
                        if verts:
                            info["start"] = verts[0]
                            info["end"] = verts[-1]
                        info["vertices"] = verts
                    except Exception:
                        pass
                elif object_name == "AcDbPolyline":
                    try:
                        coords = obj.Coordinates
                        elev = 0.0
                        try:
                            elev = float(obj.Elevation)
                        except Exception:
                            pass
                        verts = [
                            {"x": float(coords[j]), "y": float(coords[j + 1]), "z": elev}
                            for j in range(0, len(coords) - 1, 2)
                        ]
                        info["vertex_count"] = len(verts)
                        if verts:
                            info["start"] = verts[0]
                            info["end"] = verts[-1]
                        info["vertices"] = verts
                    except Exception:
                        pass
                elif object_name == "AcDbArc":
                    try:
                        info["center"] = {"x": float(obj.Center[0]), "y": float(obj.Center[1]), "z": float(obj.Center[2])}
                        info["radius"] = float(obj.Radius)
                        info["start_angle_deg"] = float(obj.StartAngle) * 180.0 / 3.141592653589793
                        info["end_angle_deg"]   = float(obj.EndAngle)   * 180.0 / 3.141592653589793
                    except Exception:
                        pass
                elif object_name == "AcDbCircle":
                    try:
                        info["center"] = {"x": float(obj.Center[0]), "y": float(obj.Center[1]), "z": float(obj.Center[2])}
                        info["radius"] = float(obj.Radius)
                    except Exception:
                        pass
                results.append(info)
        except Exception as exc:
            raise Civil3DError(f"get_selected_objects_info failed: {exc}") from exc
        return results

    # ------------------------------------------------------------------
    # COGO Points
    # ------------------------------------------------------------------

    def _get_cogo_collection(self) -> Any:
        # Try self._doc first (same source list_object_types uses), then _civil_doc()
        docs_to_try: list[Any] = [self._doc]
        civil_doc = self._civil_doc()
        if civil_doc is not self._doc:
            docs_to_try.append(civil_doc)

        for doc in docs_to_try:
            if doc is None:
                continue
            for attr in ("Points", "CogoPoints"):
                try:
                    col = getattr(doc, attr, None)
                    if col is None:
                        continue
                    _ = col.Count
                    log.debug("_get_cogo_collection: found %s from doc %r", attr, doc)
                    return col
                except Exception as exc:
                    log.debug("_get_cogo_collection: %s lookup failed on %r: %s", attr, doc, exc)

        raise Civil3DError(
            "COGO point collection not found (tried Points and CogoPoints). "
            "Ensure Civil 3D (not plain AutoCAD) is running."
        )

    def create_cogo_point(
        self,
        northing: float,
        easting: float,
        elevation: float = 0.0,
        description: str = "",
    ) -> dict[str, Any]:
        """Create a COGO point and return its properties."""
        self._ensure_connected()
        try:
            pts = self._get_cogo_collection()
            pt = pts.Add(self._pt3d(easting, northing, elevation))
            if description:
                pt.RawDescription = description
            return {
                "point_number": int(pt.Number),
                "northing": float(pt.Northing),
                "easting": float(pt.Easting),
                "elevation": float(pt.Elevation),
                "description": str(pt.RawDescription),
            }
        except Civil3DError:
            raise
        except Exception as exc:
            raise Civil3DError(f"create_cogo_point failed: {exc}") from exc

    def list_cogo_points(self, max_count: int = 50) -> list[dict[str, Any]]:
        """Return up to max_count COGO points."""
        self._ensure_connected()
        results: list[dict[str, Any]] = []
        try:
            pts = self._get_cogo_collection()
            for i in range(min(int(pts.Count), max_count)):
                pt = pts.Item(i)
                results.append({
                    "point_number": int(pt.Number),
                    "northing": float(pt.Northing),
                    "easting": float(pt.Easting),
                    "elevation": float(pt.Elevation),
                    "description": str(pt.RawDescription),
                })
        except Civil3DError:
            raise
        except Exception as exc:
            raise Civil3DError(f"list_cogo_points failed: {exc}") from exc
        return results

    def delete_cogo_point(self, point_number: int) -> bool:
        """Delete a COGO point by its point number."""
        self._ensure_connected()
        try:
            pts = self._get_cogo_collection()
            pt = pts.Find(point_number)
            if pt is None:
                raise Civil3DError(f"Point {point_number} not found.")
            pt.Delete()
            return True
        except Civil3DError:
            raise
        except Exception as exc:
            raise Civil3DError(f"delete_cogo_point failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Lines & Polylines
    # ------------------------------------------------------------------

    def create_solid_box(
        self,
        x: float,
        y: float,
        z: float,
        length: float,
        width: float,
        height: float,
        layer: str = "0",
    ) -> dict[str, Any]:
        """Create an axis-aligned 3-D box solid in model space."""
        self._ensure_connected()
        values = (x, y, z, length, width, height)
        if not all(math.isfinite(float(value)) for value in values):
            raise Civil3DError("Coordinates and dimensions must be finite numbers.")
        if any(float(value) <= 0 for value in (length, width, height)):
            raise Civil3DError("Solid dimensions must be greater than zero.")
        if not isinstance(layer, str) or not layer.strip():
            raise Civil3DError("Layer name must be a non-empty string.")

        solid = None
        try:
            acad_doc = self._acad.ActiveDocument
            self._wait_idle(acad_doc)
            self._ensure_layer(acad_doc, layer)
            center = (
                float(x) + float(length) / 2,
                float(y) + float(width) / 2,
                float(z) + float(height) / 2,
            )
            solid = acad_doc.ModelSpace.AddBox(
                self._pt3d(*center), float(length), float(width), float(height)
            )
            solid.Layer = layer
            solid.Update()
            try:
                acad_doc.Regen(1)
            except Exception:
                pass
            return {
                "object_id": self._entity_id(solid),
                "handle": str(solid.Handle),
                "layer": str(solid.Layer),
                "origin": {"x": float(x), "y": float(y), "z": float(z)},
                "length": float(length),
                "width": float(width),
                "height": float(height),
                "volume": float(length) * float(width) * float(height),
            }
        except Civil3DError:
            raise
        except Exception as exc:
            if solid is not None:
                self._discard(solid)
            raise Civil3DError(f"create_solid_box failed: {exc}") from exc

    @staticmethod
    def _matmul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
        return [
            [sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)]
            for i in range(4)
        ]

    @staticmethod
    def _axis_matrix(origin: tuple[float, float, float], d: tuple[float, float, float]) -> list[list[float]]:
        """4x4 transform sending local +Z to direction d and local origin to origin."""
        n = math.sqrt(sum(c * c for c in d))
        if n < 1e-12:
            raise Civil3DError("Direction must be a non-zero vector.")
        dx, dy, dz = (c / n for c in d)
        if dz > 1 - 1e-12:
            r = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        elif dz < -1 + 1e-12:
            r = [[1, 0, 0], [0, -1, 0], [0, 0, -1]]
        else:
            # Rodrigues rotation about axis (Z x d) by angle acos(dz)
            vx, vy = -dy, dx
            k = 1.0 / (1.0 + dz)
            r = [
                [1 - k * vy * vy, k * vx * vy, vy],
                [k * vx * vy, 1 - k * vx * vx, -vx],
                [-vy, vx, 1 - k * (vx * vx + vy * vy)],
            ]
        return [
            [*r[0], origin[0]],
            [*r[1], origin[1]],
            [*r[2], origin[2]],
            [0.0, 0.0, 0.0, 1.0],
        ]

    _PRIMITIVE_KINDS = ("cylinder", "cone", "sphere", "torus", "extrude", "revolve")

    def create_solid_primitive(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Create a curved or profile-based solid; retries while AutoCAD is busy."""
        for attempt in range(4):
            try:
                return self._create_solid_primitive(*args, **kwargs)
            except Civil3DError as exc:
                busy = "-2147418111" in str(exc) or "-2147417846" in str(exc)
                if not busy or attempt == 3:
                    raise
                time.sleep(1.0 + attempt)
        raise Civil3DError("create_solid_primitive failed")

    def _create_solid_primitive(
        self,
        kind: str,
        x: float,
        y: float,
        z: float,
        direction: tuple[float, float, float] = (0.0, 0.0, 1.0),
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
        """Create a curved or profile-based solid anchored at (x, y, z).

        The local axis is +Z and the base sits at the anchor point; ``direction``
        tilts the axis. Kinds:
        - cylinder: radius, height, optional inner_radius (hollow tube/washer).
        - cone: radius (base), height, optional top_radius (frustum).
        - sphere: radius, centred on the anchor.
        - torus: radius (ring centre line), tube_radius, centred on the anchor, ring in local XY.
        - extrude: points = [[x, y], ...] closed polygon in local XY, height, taper_angle_deg.
        - revolve: points = [[r, h], ...] profile (r >= 0 radius, h along the axis),
          swept sweep_angle_deg around the local axis.
        """
        self._ensure_connected()
        if kind not in self._PRIMITIVE_KINDS:
            raise Civil3DError(f"kind must be one of {self._PRIMITIVE_KINDS}.")
        nums = [x, y, z, radius, height, inner_radius, top_radius, tube_radius,
                taper_angle_deg, sweep_angle_deg, *direction]
        if not all(math.isfinite(float(v)) for v in nums):
            raise Civil3DError("Coordinates and dimensions must be finite numbers.")
        if not isinstance(layer, str) or not layer.strip():
            raise Civil3DError("Layer name must be a non-empty string.")
        if kind in ("cylinder", "cone", "extrude") and height <= 0:
            raise Civil3DError("height must be greater than zero.")
        if kind in ("cylinder", "cone", "sphere", "torus") and radius <= 0:
            raise Civil3DError("radius must be greater than zero.")
        if kind == "cylinder" and not 0 <= inner_radius < radius:
            raise Civil3DError("inner_radius must be in [0, radius).")
        if kind == "cone" and not 0 <= top_radius < radius:
            raise Civil3DError("top_radius must be in [0, radius).")
        if kind == "torus" and not 0 < tube_radius < radius:
            raise Civil3DError("tube_radius must be in (0, radius).")
        if kind in ("extrude", "revolve"):
            if not points or len(points) < 3 or any(len(p) != 2 for p in points):
                raise Civil3DError("points must contain at least three [a, b] pairs.")
            if kind == "revolve" and (
                any(float(p[0]) < 0 for p in points) or not 0 < sweep_angle_deg <= 360
            ):
                raise Civil3DError("Revolve needs radii >= 0 and sweep angle in (0, 360].")

        created: list[Any] = []
        curves: list[Any] = []

        def region_from(curve: Any) -> Any:
            curves.append(curve)
            arr = w32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [curve])
            return self._acad.ActiveDocument.ModelSpace.AddRegion(arr)[0]

        def polygon(pts: list[list[float]]) -> Any:
            flat = [float(c) for p in pts for c in p]
            poly = self._acad.ActiveDocument.ModelSpace.AddLightWeightPolyline(
                w32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, flat)
            )
            poly.Closed = True
            return poly

        solid = None
        try:
            acad_doc = self._acad.ActiveDocument
            self._wait_idle(acad_doc)
            self._ensure_layer(acad_doc, layer)
            ms = acad_doc.ModelSpace
            origin = self._pt3d(0.0, 0.0, 0.0)
            local = [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]]

            if kind == "cylinder":
                solid = ms.AddCylinder(self._pt3d(0, 0, height / 2), radius, height)
                created.append(solid)
                if inner_radius > 0:
                    bore = ms.AddCylinder(self._pt3d(0, 0, height / 2), inner_radius, height)
                    created.append(bore)
                    solid.Boolean(2, bore)
                    created.remove(bore)
            elif kind == "cone" and top_radius == 0:
                solid = ms.AddCone(self._pt3d(0, 0, height / 2), radius, height)
                created.append(solid)
            elif kind == "cone":
                # A frustum is a revolved trapezoid.
                profile = [[0, 0], [radius, 0], [top_radius, height], [0, height]]
                region = region_from(polygon(profile))
                created.append(region)
                solid = ms.AddRevolvedSolid(region, origin, self._pt3d(0, 1, 0), 2 * math.pi)
                created.append(solid)
                local = [[1.0, 0, 0, 0], [0, 0, -1.0, 0], [0, 1.0, 0, 0], [0, 0, 0, 1.0]]
            elif kind == "sphere":
                solid = ms.AddSphere(origin, radius)
                created.append(solid)
            elif kind == "torus":
                solid = ms.AddTorus(origin, radius, tube_radius)
                created.append(solid)
            elif kind == "extrude":
                region = region_from(polygon(points))
                created.append(region)
                solid = ms.AddExtrudedSolid(region, height, math.radians(taper_angle_deg))
                created.append(solid)
            else:  # revolve
                region = region_from(polygon(points))
                created.append(region)
                solid = ms.AddRevolvedSolid(
                    region, origin, self._pt3d(0, 1, 0), math.radians(sweep_angle_deg)
                )
                created.append(solid)
                local = [[1.0, 0, 0, 0], [0, 0, -1.0, 0], [0, 1.0, 0, 0], [0, 0, 0, 1.0]]

            for curve in curves:
                self._discard(curve)
            curves.clear()
            for ent in created:
                if ent is not solid:
                    self._discard(ent)
            try:
                solid.Handle
            except Exception:
                # Some Add* calls return an untyped dispatch; re-fetch the new entity.
                solid = ms.Item(ms.Count - 1)
            created[:] = [solid]

            matrix = self._matmul(
                self._axis_matrix((float(x), float(y), float(z)), tuple(float(c) for c in direction)),
                local,
            )
            solid.TransformBy(w32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, matrix))
            solid.Layer = layer
            solid.Update()
            try:
                acad_doc.Regen(1)
            except Exception:
                pass
            try:
                volume = float(solid.Volume)
            except Exception:
                volume = None
            return {
                "object_id": self._entity_id(solid),
                "handle": str(solid.Handle),
                "layer": str(solid.Layer),
                "kind": kind,
                "anchor": {"x": float(x), "y": float(y), "z": float(z)},
                "direction": [float(c) for c in direction],
                "volume": volume,
            }
        except Civil3DError:
            raise
        except Exception as exc:
            for ent in [*curves, *created]:
                self._discard(ent)
            raise Civil3DError(f"create_solid_primitive failed: {exc}") from exc

    def create_checkpoint(self) -> dict[str, Any]:
        """Mark the current drawing's undo history for a later restore."""
        self._ensure_connected()
        try:
            acad_doc = self._acad.ActiveDocument
            self._wait_idle(acad_doc)
            document_name = str(acad_doc.FullName or acad_doc.Name)
            acad_doc.SendCommand("_.UNDO _MARK\n")
            self._wait_idle(acad_doc)
            checkpoint_id = uuid.uuid4().hex
            self._checkpoint_id = checkpoint_id
            self._checkpoint_document = document_name
            return {
                "checkpoint_id": checkpoint_id,
                "drawing": document_name,
                "message": "Checkpoint created; only the latest checkpoint can be restored.",
            }
        except Exception as exc:
            raise Civil3DError(f"create_checkpoint failed: {exc}") from exc

    def restore_checkpoint(self, checkpoint_id: str) -> dict[str, Any]:
        """Undo drawing changes made since the latest checkpoint mark."""
        self._ensure_connected()
        if checkpoint_id != self._checkpoint_id:
            raise Civil3DError(
                "Checkpoint not found or no longer restorable. "
                "Only the latest checkpoint in this server session can be restored."
            )
        try:
            acad_doc = self._acad.ActiveDocument
            document_name = str(acad_doc.FullName or acad_doc.Name)
            if document_name != self._checkpoint_document:
                raise Civil3DError(
                    "The active drawing differs from the drawing where this checkpoint was created."
                )
            acad_doc.SendCommand("_.UNDO _BACK\n")
            self._checkpoint_id = None
            self._checkpoint_document = None
            return {
                "restored": True,
                "drawing": document_name,
                "checkpoint_id": checkpoint_id,
            }
        except Civil3DError:
            raise
        except Exception as exc:
            raise Civil3DError(f"restore_checkpoint failed: {exc}") from exc

    def create_line(
        self,
        x1: float, y1: float, z1: float,
        x2: float, y2: float, z2: float,
        layer: str = "0",
    ) -> dict[str, Any]:
        """Create a 3-D line in model space."""
        self._ensure_connected()
        line = None
        try:
            # Use the base AutoCAD document for geometry creation — the Civil 3D
            # AeccDocument wrapper does not reliably forward AddLine/Add3DPoly.
            acad_doc = self._acad.ActiveDocument
            self._wait_idle(acad_doc)
            self._ensure_layer(acad_doc, layer)
            mspace = acad_doc.ModelSpace
            line = mspace.AddLine(self._pt3d(x1, y1, z1), self._pt3d(x2, y2, z2))
            try:
                line.Handle
            except Exception:
                # Untyped dispatch returned by AddLine; re-fetch the new entity.
                line = mspace.Item(mspace.Count - 1)
            line.Layer = layer
            line.Update()
            try:
                acad_doc.Regen(1)
            except Exception:
                pass
            return {
                "object_id": self._entity_id(line),
                "handle": str(line.Handle),
                "layer": str(line.Layer),
                "start": {"x": x1, "y": y1, "z": z1},
                "end": {"x": x2, "y": y2, "z": z2},
                "length": float(line.Length),
            }
        except Civil3DError:
            raise
        except Exception as exc:
            if line is not None:
                self._discard(line)
            raise Civil3DError(f"create_line failed: {exc}") from exc

    def create_polyline(
        self,
        vertices: list[tuple[float, float, float]],
        closed: bool = False,
        layer: str = "0",
    ) -> dict[str, Any]:
        """Create a 3-D polyline from an ordered list of (x, y, z) tuples."""
        self._ensure_connected()
        if len(vertices) < 2:
            raise Civil3DError("At least 2 vertices are required.")
        pline = None
        try:
            flat = [c for v in vertices for c in v]
            pt_array = w32.VARIANT(
                pythoncom.VT_ARRAY | pythoncom.VT_R8, flat
            )
            # Use the base AutoCAD document for geometry creation — the Civil 3D
            # AeccDocument wrapper does not reliably forward Add3DPoly.
            acad_doc = self._acad.ActiveDocument
            self._wait_idle(acad_doc)
            self._ensure_layer(acad_doc, layer)
            mspace = acad_doc.ModelSpace
            pline = mspace.Add3DPoly(pt_array)
            try:
                pline.Handle
            except Exception:
                # Untyped dispatch returned by Add3DPoly; re-fetch the new entity.
                pline = mspace.Item(mspace.Count - 1)
            pline.Layer = layer
            if closed:
                pline.Closed = True
            pline.Update()
            try:
                acad_doc.Regen(1)
            except Exception:
                pass
            return {
                "object_id": self._entity_id(pline),
                "handle": str(pline.Handle),
                "layer": str(pline.Layer),
                "vertex_count": len(vertices),
                "closed": closed,
                "length": float(pline.Length),
            }
        except Civil3DError:
            raise
        except Exception as exc:
            if pline is not None:
                self._discard(pline)
            raise Civil3DError(f"create_polyline failed: {exc}") from exc

    def list_lines(self, layer_filter: str = "") -> list[dict[str, Any]]:
        """List lines and polylines in model space, optionally filtered by layer."""
        self._ensure_connected()
        results: list[dict[str, Any]] = []
        _line_types = {"AcDbLine", "AcDb3dPolyline", "AcDbPolyline", "AcDb2dPolyline"}
        try:
            for raw in self._doc.ModelSpace:
                object_name = raw.ObjectName
                if object_name not in _line_types:
                    continue
                if layer_filter and raw.Layer.lower() != layer_filter.lower():
                    continue
                # ModelSpace iteration returns base IAcadEntity; re-dispatch to the
                # concrete interface so type-specific attributes (StartPoint etc.) work.
                obj = w32.Dispatch(raw)
                info: dict[str, Any] = {
                    "object_name": object_name,
                    "handle": str(obj.Handle),
                    "layer": str(obj.Layer),
                }
                try:
                    info["length"] = float(obj.Length)
                except Exception:
                    pass
                if object_name == "AcDbLine":
                    sp, ep = obj.StartPoint, obj.EndPoint
                    info["start"] = {"x": float(sp[0]), "y": float(sp[1]), "z": float(sp[2])}
                    info["end"]   = {"x": float(ep[0]), "y": float(ep[1]), "z": float(ep[2])}
                elif object_name in ("AcDb2dPolyline", "AcDb3dPolyline"):
                    # Coordinates are a flat (x, y, z, x, y, z, ...) tuple
                    coords = obj.Coordinates
                    verts = [
                        {"x": float(coords[i]), "y": float(coords[i + 1]), "z": float(coords[i + 2])}
                        for i in range(0, len(coords) - 2, 3)
                    ]
                    info["vertex_count"] = len(verts)
                    if verts:
                        info["start"] = verts[0]
                        info["end"] = verts[-1]
                    info["vertices"] = verts
                    try:
                        info["closed"] = bool(obj.Closed)
                    except Exception:
                        pass
                elif object_name == "AcDbPolyline":
                    # LWPolyline: flat (x, y, x, y, ...) 2-D pairs; elevation separate
                    coords = obj.Coordinates
                    elev = 0.0
                    try:
                        elev = float(obj.Elevation)
                    except Exception:
                        pass
                    verts = [
                        {"x": float(coords[i]), "y": float(coords[i + 1]), "z": elev}
                        for i in range(0, len(coords) - 1, 2)
                    ]
                    info["vertex_count"] = len(verts)
                    if verts:
                        info["start"] = verts[0]
                        info["end"] = verts[-1]
                    info["vertices"] = verts
                    try:
                        info["closed"] = bool(obj.Closed)
                    except Exception:
                        pass
                results.append(info)
        except Exception as exc:
            raise Civil3DError(f"list_lines failed: {exc}") from exc
        return results

    # ------------------------------------------------------------------
    # Surfaces
    # ------------------------------------------------------------------

    def _civil_doc(self) -> Any:
        """Return the Civil 3D document interface.

        Prefers self._civil.ActiveDocument (guaranteed AeccDocument) over
        self._doc which may be a base AcadDocument if the Civil 3D interface
        was not fully acquired.
        """
        if self._civil is not None:
            try:
                land = self._civil.ActiveDocument
            except Exception:
                return self._doc
            extras = []
            for app in getattr(self, "_civil_extra", []):
                try:
                    extras.append(app.ActiveDocument)
                except Exception:
                    continue
            return _CivilDocuments(land, extras) if extras else land
        return self._doc

    def _get_surfaces(self) -> list[Any]:
        """Collect all surfaces; falls back to model-space scan if needed."""
        # Try every possible document source.  list_object_types() successfully
        # reads Surfaces.Count from self._doc directly — mirror that here first.
        docs_to_try: list[Any] = []
        docs_to_try.append(self._doc)  # same source as list_object_types
        civil_doc = self._civil_doc()
        if civil_doc is not self._doc:
            docs_to_try.append(civil_doc)

        collection_available = False
        for doc in docs_to_try:
            try:
                col = getattr(doc, "Surfaces", None)
            except Exception as exc:
                log.debug("_get_surfaces: Surfaces lookup failed on %r: %s", doc, exc)
                continue
            if col is None:
                continue
            try:
                count = int(col.Count)
            except Exception as exc:
                log.debug("_get_surfaces: Surfaces.Count failed on %r: %s", doc, exc)
                continue
            collection_available = True
            log.debug("_get_surfaces: Surfaces.Count=%d from doc %r", count, doc)
            if count == 0:
                continue
            surfaces = self._iter_com_collection(col)
            if surfaces:
                return surfaces
            log.warning(
                "_get_surfaces: Surfaces.Count=%d but iteration returned nothing; "
                "trying next document source or model-space fallback.", count
            )

        # Fallback: scan model space for TIN/Grid surface entities
        log.warning(
            "Civil 3D Surfaces collection inaccessible — "
            "falling back to model space scan."
        )
        results: list[Any] = []
        # ObjectName examples: AeccDbTinSurface, AeccDbGridSurface,
        # AeccDbTinVolumeSurface, AeccDbGridVolumeSurface
        _surface_types = (
            "aeccdbtin", "aeccdbgrid", "aeccdbtinvolume", "aeccdbgridvolume",
            "surface",  # catch-all in case naming convention differs
        )
        scan_doc = self._doc
        try:
            for raw in scan_doc.ModelSpace:
                obj_name = getattr(raw, "ObjectName", "").lower()
                if any(t in obj_name for t in _surface_types):
                    obj = w32.Dispatch(raw)
                    if getattr(obj, "Name", None) is not None:
                        results.append(obj)
        except Exception as exc:
            log.warning("Surface model-space scan failed: %s", exc)

        if results or collection_available:
            return results
        raise Civil3DError(
            "Surfaces collection not found. "
            "Ensure Civil 3D (not plain AutoCAD) is running."
        )

    def list_surfaces(self) -> list[dict[str, Any]]:
        """List all TIN/Grid surfaces with basic elevation statistics."""
        self._ensure_connected()
        results: list[dict[str, Any]] = []
        try:
            for surf in self._get_surfaces():
                info: dict[str, Any] = {
                    "name": str(surf.Name),
                    "description": str(getattr(surf, "Description", "")),
                    "style": str(getattr(surf, "StyleName", "")),
                    "object_id": str(surf.ObjectID),
                }
                try:
                    st = surf.Statistics
                    info["min_elevation"] = float(st.MinElevation)
                    info["max_elevation"] = float(st.MaxElevation)
                    info["mean_elevation"] = float(st.MeanElevation)
                    info["point_count"] = int(st.NumberOfPoints)
                    info["triangle_count"] = int(st.NumberOfTriangles)
                except Exception:
                    pass
                results.append(info)
        except Civil3DError:
            raise
        except Exception as exc:
            raise Civil3DError(f"list_surfaces failed: {exc}") from exc
        return results

    def get_surface_info(self, surface_name: str) -> dict[str, Any]:
        """Return detailed statistics for a named surface."""
        self._ensure_connected()
        surf = self._find_surface(surface_name)
        info: dict[str, Any] = {
            "name": str(surf.Name),
            "description": str(getattr(surf, "Description", "")),
            "style": str(getattr(surf, "StyleName", "")),
        }
        try:
            st = surf.Statistics
            info.update({
                "min_elevation": float(st.MinElevation),
                "max_elevation": float(st.MaxElevation),
                "mean_elevation": float(st.MeanElevation),
                "point_count": int(st.NumberOfPoints),
                "triangle_count": int(st.NumberOfTriangles),
                "area_2d": float(st.Area2d),
                "area_3d": float(st.Area3d),
                "min_x": float(st.MinX),
                "min_y": float(st.MinY),
                "max_x": float(st.MaxX),
                "max_y": float(st.MaxY),
            })
        except Exception as exc:
            log.warning("Could not read surface statistics: %s", exc)
        return info

    def sample_surface_elevation(
        self,
        surface_name: str,
        easting: float,
        northing: float,
    ) -> dict[str, Any]:
        """Sample interpolated elevation of a surface at (easting, northing)."""
        self._ensure_connected()
        surf = self._find_surface(surface_name)
        try:
            elev = surf.FindElevationAtXY(easting, northing)
            return {
                "surface_name": surface_name,
                "easting": easting,
                "northing": northing,
                "elevation": float(elev),
            }
        except Exception as exc:
            raise Civil3DError(
                f"sample_surface_elevation failed "
                f"(point may be outside surface extent): {exc}"
            ) from exc

    def _find_surface(self, name: str) -> Any:
        for surf in self._get_surfaces():
            if getattr(surf, "Name", "").lower() == name.lower():
                return surf
        raise Civil3DError(f"Surface '{name}' not found in the active drawing.")

    
    def list_surface_definition(self, surface_name: str) -> dict[str, Any]:
        """List all definition items for a surface.

        Each collection (boundaries, breaklines, etc.) is queried individually
        so a failure on one category does not abort the whole call.
        Uses _iter_com_collection() to avoid silent IEnumVARIANT failures.
        """
        self._ensure_connected()
        surf = self._find_surface(surface_name)
        result: dict[str, Any] = {"surface_name": surface_name, "definitions": {}}

        # Obtain the DataDefinition object — not all surface types expose it.
        try:
            defn = surf.DataDefinition
        except Exception as exc:
            result["definitions_error"] = (
                f"DataDefinition not available for this surface type: {exc}"
            )
            return result

        collection_map = [
            ("Boundaries",     "boundaries"),
            ("Breaklines",     "breaklines"),
            ("Contours",       "contours"),
            ("DEMFiles",       "dem_files"),
            ("DrawingObjects", "drawing_objects"),
            ("PointFiles",     "point_files"),
            ("PointGroups",    "point_groups"),
            ("SurveyPoints",   "survey_points"),
            ("SurveyFigures",  "survey_figures"),
        ]

        for attr, label in collection_map:
            try:
                col = getattr(defn, attr, None)
                if col is None:
                    result["definitions"][label] = {"count": 0, "items": []}
                    continue
                raw_items = self._iter_com_collection(col)
                items: list[dict[str, Any]] = []
                for item in raw_items:
                    item_info: dict[str, Any] = {
                        "name": str(getattr(item, "Name", repr(item)))
                    }
                    for prop in ("Description", "Type", "BreaklineType",
                                 "BoundaryType", "FileName", "StyleName"):
                        try:
                            val = getattr(item, prop, None)
                            if val is not None:
                                item_info[prop.lower()] = str(val)
                        except Exception:
                            pass
                    items.append(item_info)
                result["definitions"][label] = {"count": len(items), "items": items}
            except Exception as exc:
                result["definitions"][label] = {"error": str(exc)}

        return result

    

    def create_surface_from_eg_cogo_points(
        self,
        surface_name: str = "EG_Surface",
        profile_name: str = "EG_Profile",
        point_description: str = "EG",
    ) -> dict[str, Any]:
        """Create a TIN from matching COGO points and add surface profiles."""
        self._ensure_connected()
        surface_name = surface_name.strip()
        profile_name = profile_name.strip()
        point_description = point_description.strip()
        if not surface_name or not profile_name or not point_description:
            raise Civil3DError(
                "surface_name, profile_name, and point_description must not be empty."
            )

        doc = self._civil_doc()
        try:
            surfaces = doc.Surfaces
            point_groups = doc.PointGroups
            surface_style = doc.SurfaceStyles.Item(0).Name
            layer = doc.Layers.Item(0).Name
        except Exception as exc:
            raise Civil3DError(
                f"Required Civil 3D surface, point-group, surface-style, or layer "
                f"collection is unavailable: {exc}"
            ) from exc

        # Profile styles are only needed for profiles; an empty collection must
        # not block surface creation.
        profile_style: str | None = None
        try:
            profile_style = doc.ProfileStyles.Item(0).Name
        except Exception as exc:
            log.warning("No profile style available; profiles will be skipped: %s", exc)

        try:
            for existing in self._iter_com_collection(surfaces):
                if str(getattr(existing, "Name", "")).casefold() == surface_name.casefold():
                    raise Civil3DError(
                        f"Surface '{surface_name}' already exists; choose another name."
                    )
        except Civil3DError:
            raise
        except Exception as exc:
            raise Civil3DError(f"Could not check existing surfaces: {exc}") from exc

        matching_points: list[int] = []
        try:
            target_description = point_description.casefold()
            for point in self._iter_com_collection(self._get_cogo_collection()):
                matches_description = False
                for attr in ("RawDescription", "Description", "Name", "PointName"):
                    try:
                        value = getattr(point, attr, "")
                    except Exception:
                        continue
                    if value is not None and str(value).strip().casefold() == target_description:
                        matches_description = True
                        break
                if matches_description:
                    matching_points.append(int(point.Number))
        except Civil3DError:
            raise
        except Exception as exc:
            raise Civil3DError(f"Could not inspect COGO points: {exc}") from exc

        if not matching_points:
            raise Civil3DError(
                f"No COGO points have description or name '{point_description}'. "
                "No surface or profiles were created."
            )

        alignments = self._get_alignments()
        profile_targets: list[tuple[Any, Any]] = []
        for alignment in alignments:
            alignment_name = str(alignment.Name)
            profiles = getattr(alignment, "Profiles", None)
            if profiles is None:
                raise Civil3DError(
                    f"Alignment '{alignment_name}' has no Profiles collection; "
                    "no surface or profiles were created."
                )
            profile_targets.append((alignment, profiles))

        group_name = f"{surface_name}_EG_{uuid.uuid4().hex[:8]}"
        tin_data = None
        attempted_versions = [
            prog_id.split("AeccApplication.", 1)[-1]
            for prog_id in self._CIVIL3D_PROG_IDS
        ]
        for version in attempted_versions:
            try:
                tin_data = self._acad.GetInterfaceObject(
                    f"AeccXLand.AeccTinCreationData.{version}"
                )
                if tin_data is not None:
                    break
            except Exception:
                continue
        if tin_data is None:
            raise Civil3DError(
                "Could not create AeccTinCreationData. Tried Civil 3D versions: "
                + ", ".join(attempted_versions)
            )

        try:
            tin_data.Name = surface_name
            tin_data.Description = (
                f"TIN surface created from COGO points matching '{point_description}'."
            )
            tin_data.Layer = layer
            tin_data.BaseLayer = layer
            tin_data.Style = surface_style
        except Exception as exc:
            raise Civil3DError(f"Could not configure TIN surface data: {exc}") from exc

        point_group = None
        surface = None
        try:
            point_group = point_groups.Add(group_name)
            point_group.QueryBuilder.IncludeNumbers = ",".join(
                str(number) for number in matching_points
            )
            surface = surfaces.AddTinSurface(tin_data)
            surface.PointGroups.Add(point_group)
            surface.Rebuild()
        except Exception as exc:
            cleanup_errors: list[str] = []
            for item, label in ((surface, "surface"), (point_group, "point group")):
                if item is None:
                    continue
                try:
                    item.Delete()
                except Exception as cleanup_exc:
                    cleanup_errors.append(f"{label}: {cleanup_exc}")
            cleanup_note = (
                f" Cleanup also failed ({'; '.join(cleanup_errors)})."
                if cleanup_errors else ""
            )
            raise Civil3DError(
                f"Could not create or populate TIN surface '{surface_name}': {exc}."
                f"{cleanup_note}"
            ) from exc

        profiles_created: list[dict[str, Any]] = []
        profile_errors: list[dict[str, str]] = []
        for alignment, profiles in profile_targets:
            alignment_name = str(alignment.Name)
            if profile_style is None:
                profile_errors.append({
                    "alignment_name": alignment_name,
                    "error": "Drawing has no profile styles; profile skipped.",
                })
                continue
            try:
                existing_names = {
                    str(getattr(item, "Name", "")).casefold()
                    for item in self._iter_com_collection(profiles)
                }
                if profile_name.casefold() in existing_names:
                    profiles_created.append({
                        "alignment_name": alignment_name,
                        "profile_name": profile_name,
                        "status": "already_exists",
                    })
                    continue
                profiles.AddFromSurface(
                    profile_name,
                    1,
                    profile_style,
                    surface_name,
                    float(alignment.StartingStation),
                    float(alignment.EndingStation),
                    "0",
                )
                profiles_created.append({
                    "alignment_name": alignment_name,
                    "profile_name": profile_name,
                    "status": "created",
                })
            except Exception as exc:
                log.warning(
                    "Could not create surface profile '%s' for alignment '%s': %s",
                    profile_name, alignment_name, exc,
                )
                profile_errors.append({
                    "alignment_name": alignment_name,
                    "error": str(exc),
                })

        return {
            "status": "partial" if profile_errors else "completed",
            "surface_name": surface_name,
            "point_group": group_name,
            "point_description": point_description,
            "matched_point_count": len(matching_points),
            "matched_point_numbers": matching_points,
            "profiles": profiles_created,
            "profile_errors": profile_errors,
            "profile_views_created": False,
        }

    # ------------------------------------------------------------------
    # Alignments
    # ------------------------------------------------------------------

    @staticmethod
    def _iter_com_collection(col: Any) -> list[Any]:
        """Iterate a Civil 3D COM collection safely.

        Tries multiple strategies in order:
        1. 0-based Item(i) index access
        2. 1-based Item(i) index access
        3. for-loop on the raw collection
        All exceptions are logged so failures are visible in the server log.
        """
        items: list[Any] = []
        try:
            count = int(col.Count)
        except Exception as exc:
            log.debug("_iter_com_collection: cannot read Count: %s", exc)
            count = -1

        if count > 0:
            # Strategy 1: 0-based Item(i)
            zero_items: list[Any] = []
            for i in range(count):
                try:
                    zero_items.append(col.Item(i))
                except Exception as exc:
                    log.debug("_iter_com_collection Item(%d) 0-based failed: %s", i, exc)
                    break
            if len(zero_items) == count:
                return zero_items

            # Strategy 2: 1-based Item(i)
            one_items: list[Any] = []
            for i in range(1, count + 1):
                try:
                    one_items.append(col.Item(i))
                except Exception as exc:
                    log.debug("_iter_com_collection Item(%d) 1-based failed: %s", i, exc)
                    break
            if len(one_items) == count:
                return one_items

            # Use whichever partial set is longer
            best = zero_items if len(zero_items) >= len(one_items) else one_items
            if best:
                log.debug(
                    "_iter_com_collection: returning partial index results (%d/%d)",
                    len(best), count,
                )
                return best

        # Strategy 3: raw for-loop
        try:
            for item in col:
                items.append(item)
        except Exception as exc:
            log.debug("_iter_com_collection raw for-loop failed: %s", exc)

        if not items and count > 0:
            log.warning(
                "_iter_com_collection: Count=%d but all iteration strategies failed.",
                count,
            )
        return items

    def _get_alignments(self) -> list[Any]:
        """Collect all alignments from siteless and site-based collections."""
        results: list[Any] = []
        collection_available = False

        # Try self._doc first (same source that list_object_types uses
        # successfully for AlignmentsSiteless.Count), then _civil_doc().
        docs_to_try: list[Any] = [self._doc]
        civil_doc = self._civil_doc()
        if civil_doc is not self._doc:
            docs_to_try.append(civil_doc)

        for doc in docs_to_try:
            # Siteless alignments (Civil 3D 2016+)
            siteless = getattr(doc, "AlignmentsSiteless", None)
            if siteless is not None:
                try:
                    int(siteless.Count)
                    collection_available = True
                except Exception as exc:
                    log.debug(
                        "_get_alignments: AlignmentsSiteless.Count failed: %s", exc
                    )
                found = self._iter_com_collection(siteless)
                log.debug(
                    "_get_alignments: AlignmentsSiteless from doc %r -> %d items",
                    doc, len(found),
                )
                results.extend(found)

            # Site-based alignments
            sites = getattr(doc, "Sites", None)
            if sites is not None:
                try:
                    int(sites.Count)
                    collection_available = True
                except Exception as exc:
                    log.debug("_get_alignments: Sites.Count failed: %s", exc)
                for site in self._iter_com_collection(sites):
                    al_col = getattr(site, "Alignments", None)
                    if al_col is not None:
                        try:
                            int(al_col.Count)
                            collection_available = True
                        except Exception as exc:
                            log.debug(
                                "_get_alignments: site Alignments.Count failed: %s",
                                exc,
                            )
                        results.extend(self._iter_com_collection(al_col))

            if results:
                break  # found via this document source; stop

        # Last-resort fallback: scan model space for alignment entities
        if not results:
            log.warning(
                "Civil 3D alignment collections empty or inaccessible — "
                "falling back to model space scan."
            )
            try:
                for raw in self._doc.ModelSpace:
                    obj_name = getattr(raw, "ObjectName", "")
                    if "alignment" in obj_name.lower():
                        obj = w32.Dispatch(raw)
                        if (
                            getattr(obj, "Name", None) is not None
                            and getattr(obj, "StartingStation", None) is not None
                        ):
                            results.append(obj)
            except Exception as exc:
                log.warning("Alignment model-space scan failed: %s", exc)

        if results or collection_available:
            return results
        raise Civil3DError(
            "Alignments collection not found. "
            "Ensure Civil 3D (not plain AutoCAD) is running."
        )

    def _find_alignment(self, name: str) -> Any:
        for al in self._get_alignments():
            if al.Name.lower() == name.lower():
                return al
        raise Civil3DError(f"Alignment '{name}' not found in the active drawing.")

    def list_alignments(self) -> list[dict[str, Any]]:
        """List all alignments with station range and length."""
        self._ensure_connected()
        results: list[dict[str, Any]] = []
        try:
            for al in self._get_alignments():
                results.append({
                    "name": str(al.Name),
                    "description": str(getattr(al, "Description", "")),
                    "style": str(getattr(al, "StyleName", "")),
                    "object_id": str(al.ObjectID),
                    "length": float(al.Length),
                    "start_station": float(al.StartingStation),
                    "end_station": float(al.EndingStation),
                    "station_index_increment": float(
                        getattr(al, "StationIndexIncrement", 0)
                    ),
                })
        except Civil3DError:
            raise
        except Exception as exc:
            raise Civil3DError(f"list_alignments failed: {exc}") from exc
        return results

    def get_alignment_info(self, alignment_name: str) -> dict[str, Any]:
        """Return geometry breakdown (tangents, curves, spirals) for an alignment."""
        self._ensure_connected()
        al = self._find_alignment(alignment_name)
        info: dict[str, Any] = {
            "name": str(al.Name),
            "description": str(getattr(al, "Description", "")),
            "length": float(al.Length),
            "start_station": float(al.StartingStation),
            "end_station": float(al.EndingStation),
        }
        entities: list[dict[str, Any]] = []
        try:
            ents = al.Entities
            for i in range(ents.Count):
                ent = ents.EntityAt(i)
                ent_info: dict[str, Any] = {
                    "index": i,
                    "type": str(ent.EntityType),
                    "start_station": float(ent.StartStation),
                    "end_station": float(ent.EndStation),
                    "length": float(ent.Length),
                }
                for attr in ("Radius", "TangentLength", "Delta", "Direction"):
                    val = getattr(ent, attr, None)
                    if val is not None:
                        try:
                            ent_info[attr.lower()] = float(val)
                        except (TypeError, ValueError):
                            ent_info[attr.lower()] = str(val)
                entities.append(ent_info)
        except Exception as exc:
            log.warning("Could not read alignment entities: %s", exc)
        info["entities"] = entities
        return info

    def get_station_offset(
        self,
        alignment_name: str,
        easting: float,
        northing: float,
    ) -> dict[str, Any]:
        """
        Project (easting, northing) onto an alignment.
        Returns station and perpendicular offset.
        """
        self._ensure_connected()
        al = self._find_alignment(alignment_name)
        try:
            # StationOffset is a COM method with two by-reference out-parameters.
            # We pass mutable VARIANT objects; Civil 3D writes the values back.
            station_var = self._out_double()
            offset_var = self._out_double()
            al.StationOffset(easting, northing, station_var, offset_var)
            return {
                "alignment_name": alignment_name,
                "easting": easting,
                "northing": northing,
                "station": float(station_var.value),
                "offset": float(offset_var.value),
            }
        except Exception as exc:
            raise Civil3DError(f"get_station_offset failed: {exc}") from exc

    def list_profiles(self, alignment_name: str) -> list[dict[str, Any]]:
        """List all vertical profiles attached to a named alignment."""
        self._ensure_connected()
        al = self._find_alignment(alignment_name)
        results: list[dict[str, Any]] = []
        try:
            profiles = getattr(al, "Profiles", None)
            if profiles is None:
                raise Civil3DError(
                    f"Alignment '{alignment_name}' has no Profiles collection. "
                    "Ensure Civil 3D (not plain AutoCAD) is running."
                )
            for prof in self._iter_com_collection(profiles):
                info: dict[str, Any] = {
                    "name": str(prof.Name),
                    "description": str(getattr(prof, "Description", "")),
                    "style": str(getattr(prof, "StyleName", "")),
                    "object_id": str(getattr(prof, "ObjectID", "")),
                }
                for attr in ("Length", "StartingStation", "EndingStation",
                              "MinElevation", "MaxElevation"):
                    val = getattr(prof, attr, None)
                    if val is not None:
                        try:
                            info[attr[0].lower() + attr[1:]] = float(val)
                        except (TypeError, ValueError):
                            pass
                results.append(info)
        except Civil3DError:
            raise
        except Exception as exc:
            raise Civil3DError(f"list_profiles failed: {exc}") from exc
        return results

    def get_profile_info(self, alignment_name: str, profile_name: str) -> dict[str, Any]:
        """Return full geometry breakdown of a named vertical profile."""
        self._ensure_connected()
        al = self._find_alignment(alignment_name)
        try:
            profiles = getattr(al, "Profiles", None)
            if profiles is None:
                raise Civil3DError(
                    f"Alignment '{alignment_name}' has no Profiles collection."
                )
            prof = None
            for p in self._iter_com_collection(profiles):
                if p.Name.lower() == profile_name.lower():
                    prof = p
                    break
            if prof is None:
                raise Civil3DError(
                    f"Profile '{profile_name}' not found on alignment '{alignment_name}'."
                )
        except Civil3DError:
            raise
        except Exception as exc:
            raise Civil3DError(f"get_profile_info: lookup failed: {exc}") from exc

        info: dict[str, Any] = {
            "alignment_name": alignment_name,
            "name": str(prof.Name),
            "description": str(getattr(prof, "Description", "")),
            "style": str(getattr(prof, "StyleName", "")),
            "object_id": str(getattr(prof, "ObjectID", "")),
        }
        for attr in ("Length", "StartingStation", "EndingStation",
                     "MinElevation", "MaxElevation"):
            val = getattr(prof, attr, None)
            if val is not None:
                try:
                    info[attr[0].lower() + attr[1:]] = float(val)
                except (TypeError, ValueError):
                    pass

        # Entity breakdown (PVIs, tangents, curves)
        entities: list[dict[str, Any]] = []
        try:
            ents = prof.Entities
            count = int(ents.Count)
            for i in range(count):
                try:
                    ent = ents.EntityAt(i)
                except Exception:
                    try:
                        ent = ents.Item(i)
                    except Exception:
                        continue
                ent_info: dict[str, Any] = {
                    "index": i,
                    "type": str(getattr(ent, "EntityType", "Unknown")),
                }
                for attr in ("StartStation", "EndStation", "Length",
                             "StartElevation", "EndElevation",
                             "Radius", "K", "HighLowPtStation", "HighLowPtElevation"):
                    val = getattr(ent, attr, None)
                    if val is not None:
                        try:
                            ent_info[attr[0].lower() + attr[1:]] = float(val)
                        except (TypeError, ValueError):
                            pass
                entities.append(ent_info)
        except Exception as exc:
            log.warning("Could not read profile entities for '%s': %s", profile_name, exc)
        info["entities"] = entities

        # PVI list
        pvis: list[dict[str, Any]] = []
        try:
            pvi_col = prof.PVIs
            for pvi in self._iter_com_collection(pvi_col):
                pvi_info: dict[str, Any] = {}
                for attr in ("Station", "Elevation", "CurveLength"):
                    val = getattr(pvi, attr, None)
                    if val is not None:
                        try:
                            pvi_info[attr[0].lower() + attr[1:]] = float(val)
                        except (TypeError, ValueError):
                            pass
                pvis.append(pvi_info)
        except Exception as exc:
            log.warning("Could not read PVIs for '%s': %s", profile_name, exc)
        info["pvis"] = pvis

        return info
