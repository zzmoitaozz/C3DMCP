"""Load user-authored MCP tool modules from the bundled and user directories."""

from __future__ import annotations

import importlib.util
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from civil3d_mcp.paths import user_tools_dir

log = logging.getLogger("civil3d_mcp.custom_tools")
_TOOLS_DIRECTORY = Path(__file__).parent


def register_custom_tools(
    mcp: Any,
    client: Any,
    run_com: Callable[..., Any],
    tools_directory: Path | None = None,
) -> int:
    """Register custom tool modules.

    With an explicit directory, only that directory is loaded and errors propagate.
    Otherwise the bundled package directory (strict) and the per-user directory
    (invalid modules are logged and skipped) are loaded.
    """
    if tools_directory is not None:
        return _register_directory(mcp, client, run_com, tools_directory, "custom_tools", strict=True)
    registered = _register_directory(mcp, client, run_com, _TOOLS_DIRECTORY, "custom_tools", strict=True)
    user_dir = user_tools_dir()
    if user_dir.is_dir():
        registered += _register_directory(mcp, client, run_com, user_dir, "user_tools", strict=False)
    return registered


def _register_directory(
    mcp: Any,
    client: Any,
    run_com: Callable[..., Any],
    tools_directory: Path,
    namespace: str,
    *,
    strict: bool,
) -> int:
    registered = 0
    for module_path in sorted(tools_directory.glob("*.py")):
        if module_path.name.startswith("_"):
            continue
        try:
            _load_module(mcp, client, run_com, module_path, f"civil3d_mcp.{namespace}")
        except Exception:
            if strict:
                raise
            log.exception("Skipping invalid user tool module %s", module_path)
            continue
        registered += 1
    return registered


def _load_module(
    mcp: Any, client: Any, run_com: Callable[..., Any], module_path: Path, package_name: str
) -> None:
    module_name = f"{package_name}.{module_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load custom MCP tool module: {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        register = getattr(module, "register", None)
        if not callable(register):
            raise TypeError(f"Custom MCP tool module has no callable register(): {module_path}")
        register(mcp, client, run_com)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    log.info("Registered custom MCP tool module %s", module_path.stem)
