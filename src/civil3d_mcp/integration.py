"""Install helpers: link Claude Desktop and create an AutoCAD plug-in bundle."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

from .paths import claude_config_paths, executable_command

SERVER_KEY = "civil3d-mcp"


def link_claude_desktop() -> list[str]:
    """Merge the server into every Claude Desktop config; return updated paths."""
    command, args = executable_command()
    entry = {"command": command, "args": args}
    updated: list[str] = []
    candidates = [p for p in claude_config_paths() if p.parent.is_dir()]
    for path in candidates:
        data: dict = {}
        if path.exists():
            raw = path.read_text(encoding="utf-8-sig").strip()
            if raw:
                data = json.loads(raw)
            shutil.copy2(path, path.with_name(f"{path.name}.bak-{int(time.time())}"))
        servers = data.setdefault("mcpServers", {})
        servers[SERVER_KEY] = entry
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        updated.append(str(path))
    return updated


def install_autocad_bundle(target_root: Path | None = None) -> Path:
    """Create %ProgramData%-style ApplicationPlugins bundle holding the exe."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("install-autocad must be run from the built civil3d-mcp.exe.")
    root = target_root or Path(os.environ.get("APPDATA", "")) / "Autodesk" / "ApplicationPlugins"
    bundle = root / "Civil3DMCP.bundle"
    contents = bundle / "Contents"
    contents.mkdir(parents=True, exist_ok=True)
    exe_name = Path(sys.executable).name
    shutil.copy2(sys.executable, contents / exe_name)
    (bundle / "PackageContents.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ApplicationPackage SchemaVersion="1.0" AutodeskProduct="AutoCAD" '
        'Name="Civil3DMCP" Description="Civil 3D MCP server and tool builder" '
        'AppVersion="1.0.0" ProductCode="{6F1D2C1A-3B7E-4C57-9A55-0C3D4D4C0A01}" '
        'UpgradeCode="{6F1D2C1A-3B7E-4C57-9A55-0C3D4D4C0A02}">\n'
        '  <CompanyDetails Name="Civil3DMCP" />\n'
        '  <Components>\n'
        '    <ComponentEntry AppName="Civil3DMCP" ModuleName="./Contents/' + exe_name + '" '
        'AppType=".EXE" />\n'
        '  </Components>\n'
        '</ApplicationPackage>\n',
        encoding="utf-8",
    )
    return bundle
