"""Load user-authored MCP tool modules from this package directory."""

from __future__ import annotations

import importlib.util
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("civil3d_mcp.custom_tools")
_TOOLS_DIRECTORY = Path(__file__).parent


def register_custom_tools(
    mcp: Any,
    client: Any,
    run_com: Callable[..., Any],
    tools_directory: Path = _TOOLS_DIRECTORY,
) -> int:
    """Import and register each custom tool module; fail clearly on invalid modules."""
    registered = 0
    package_name = __name__

    for module_path in sorted(tools_directory.glob("*.py")):
        if module_path.name.startswith("_"):
            continue

        module_name = f"{package_name}.{module_path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load custom MCP tool module: {module_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
        register = getattr(module, "register", None)
        if not callable(register):
            raise TypeError(f"Custom MCP tool module has no callable register(): {module_path}")

        register(mcp, client, run_com)
        registered += 1
        log.info("Registered custom MCP tool module %s", module_path.stem)

    return registered
