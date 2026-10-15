"""Filesystem locations shared by the server, web UI and installers."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Civil3DMCP"


def user_tools_dir() -> Path:
    """Writable folder for user-authored tools (survives exe upgrades)."""
    override = os.environ.get("CIVIL3D_MCP_TOOLS_DIR")
    return Path(override) if override else app_data_dir() / "custom_tools"


def executable_command() -> tuple[str, list[str]]:
    """Command Claude Desktop should run to start this server."""
    if getattr(sys, "frozen", False):
        return sys.executable, ["serve"]
    return sys.executable, ["-m", "civil3d_mcp", "serve"]


def claude_config_paths() -> list[Path]:
    """Existing Claude Desktop config files (Store/MSIX and classic installs)."""
    paths: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        packages = Path(local) / "Packages"
        if packages.is_dir():
            for package in packages.glob("Claude_*"):
                paths.append(
                    package / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json"
                )
    return paths
