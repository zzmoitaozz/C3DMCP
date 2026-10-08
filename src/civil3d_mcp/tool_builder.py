"""Local web UI for generating custom Civil 3D MCP tool scaffolds."""

from __future__ import annotations

import argparse
import ast
import hmac
import http.server
import json
import keyword
import logging
import re
import secrets
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from civil3d_mcp.paths import user_tools_dir

log = logging.getLogger("civil3d_mcp.tool_builder")
BUNDLED_TOOLS_DIRECTORY = Path(__file__).with_name("custom_tools")
TOOLS_DIRECTORY = user_tools_dir()
_KNOWN_BUILTIN_TOOLS = frozenset(
    {
        "list_surfaces", "get_surface_info", "sample_surface_elevation", "list_surface_definition",
        "create_line", "create_polyline", "list_lines",
        "get_drawing_info", "list_civil_object_types", "get_selected_objects_info",
        "get_corridor_info",
        "create_cogo_point", "list_cogo_points", "delete_cogo_point",
        "list_alignments", "get_alignment_info", "get_station_offset", "list_profiles", "get_profile_info",
    }
)
MAX_REQUEST_BYTES = 32_768
MAX_PARAMETERS = 20
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PYTHON_TYPES = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
}
_OPTIONAL_DEFAULTS = {
    "string": '""',
    "integer": "0",
    "number": "0.0",
    "boolean": "False",
}
_RESERVED_PARAMETER_NAMES = {"mcp", "client", "run_com"}

_PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Civil 3D MCP Tool Builder</title>
  <style>
    :root { color-scheme: light; font: 16px/1.5 system-ui, sans-serif; }
    body { margin: 0; background: #f3f5f8; color: #172033; }
    main { max-width: 820px; margin: 40px auto; padding: 28px; background: white;
           border: 1px solid #d9e0ea; border-radius: 12px; }
    h1 { margin-top: 0; font-size: 1.6rem; }
    p, .hint { color: #526176; }
    label { display: block; margin: 14px 0 5px; font-weight: 600; }
    input, textarea, select, button { font: inherit; }
    input[type=text], textarea, select { box-sizing: border-box; width: 100%;
      padding: 9px 10px; border: 1px solid #b8c4d4; border-radius: 6px; }
    textarea { min-height: 84px; resize: vertical; }
    .parameter { display: grid; grid-template-columns: 1.2fr 1fr auto auto;
      align-items: end; gap: 12px; margin: 12px 0; }
    .required { padding-bottom: 10px; white-space: nowrap; }
    button { padding: 9px 14px; border: 0; border-radius: 6px; cursor: pointer;
      background: #155eef; color: white; font-weight: 600; }
    button.secondary { background: #e8edf5; color: #172033; }
    button.remove { background: #fff0f0; color: #a11919; }
    #result { min-height: 1.5em; margin-top: 16px; white-space: pre-wrap; }
    #result.error { color: #a11919; }
    @media (max-width: 650px) {
      main { margin: 12px; padding: 18px; }
      .parameter { grid-template-columns: 1fr 1fr; }
    }
  </style>
</head>
<body>
<main data-mode="__MODE__">
  <h1>Civil 3D MCP Tool Builder</h1>
  <section id="status">
    <h2>MCP server status</h2>
    <p id="status-text">Checking…</p>
    <p id="status-dir" class="hint"></p>
    <p id="status-tools" class="hint"></p>
    <button class="secondary" id="link-claude" type="button">Link Claude Desktop</button>
  </section>
  <p>Create a Python scaffold. Complete its handler, then restart the MCP server (restart Claude Desktop).</p>
  <form id="tool-form">
    <label for="name">Tool name</label>
    <input id="name" name="name" required pattern="[a-z][a-z0-9_]{0,63}"
      placeholder="get_parcel_area" autocomplete="off">
    <label for="description">Description</label>
    <textarea id="description" name="description" maxlength="1000" required
      placeholder="Describe what the tool does"></textarea>
    <div id="token-section" hidden>
      <label for="token">LAN access token</label>
      <input id="token" type="text" autocomplete="off" spellcheck="false">
    </div>
    <h2>Parameters</h2>
    <p class="hint">Parameter names use lowercase letters, digits, and underscores. Required parameters are listed first in the generated function.</p>
    <div id="parameters"></div>
    <button class="secondary" id="add-parameter" type="button">Add parameter</button>
    <p><button type="submit">Generate tool scaffold</button></p>
  </form>
  <div id="result" role="status" aria-live="polite"></div>
</main>
<script>
const form = document.querySelector("#tool-form");
const parameterList = document.querySelector("#parameters");
const result = document.querySelector("#result");
const mode = document.querySelector("main").dataset.mode;
document.querySelector("#token-section").hidden = mode !== "lan";

const authHeader = () => "Bearer " + document.querySelector("#token").value;
const apiHeaders = () => (mode === "lan" ? { Authorization: authHeader() } : {});

async function refreshStatus() {
  const text = document.querySelector("#status-text");
  try {
    const response = await fetch("/api/status", { headers: apiHeaders() });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Unauthorized");
    text.textContent = "MCP server: running | Civil 3D: " +
      (data.civil3d_connected ? "connected" : "not connected");
    text.className = data.civil3d_connected ? "" : "hint";
    document.querySelector("#status-dir").textContent = "Custom tools folder: " + data.tools_directory;
    document.querySelector("#status-tools").textContent =
      "Custom tools: " + (data.custom_tools.join(", ") || "none") +
      (data.builtin_tool_count ? " | Built-in tools: " + data.builtin_tool_count : "");
  } catch (error) {
    text.textContent = "MCP server: unreachable (" + error.message + ")";
    text.className = "error";
  }
}
document.querySelector("#link-claude").addEventListener("click", async () => {
  try {
    const response = await fetch("/api/claude/link", { method: "POST", headers: apiHeaders() });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Request failed");
    result.textContent = "Linked Claude Desktop: " + data.updated.join(", ") + ". Restart Claude Desktop.";
    result.className = "";
  } catch (error) {
    result.textContent = error.message;
    result.className = "error";
  }
});
refreshStatus();
setInterval(refreshStatus, 5000);

function addParameter() {
  if (parameterList.children.length >= 20) {
    result.textContent = "A tool can have at most 20 parameters.";
    result.className = "error";
    return;
  }
  const row = document.createElement("div");
  row.className = "parameter";
  row.innerHTML = `<div><label>Parameter name</label><input class="param-name" required
    pattern="[a-z][a-z0-9_]{0,63}" placeholder="parcel_id"></div>
    <div><label>Type</label><select class="param-type">
      <option value="string">String</option><option value="integer">Integer</option>
      <option value="number">Number</option><option value="boolean">Boolean</option>
    </select></div>
    <label class="required"><input class="param-required" type="checkbox" checked> Required</label>
    <button class="remove" type="button">Remove</button>`;
  row.querySelector(".remove").addEventListener("click", () => row.remove());
  parameterList.append(row);
}

document.querySelector("#add-parameter").addEventListener("click", addParameter);
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  result.className = "";
  result.textContent = "Creating scaffold…";
  const payload = {
    name: form.elements.name.value,
    description: form.elements.description.value,
    parameters: [...parameterList.querySelectorAll(".parameter")].map((row) => ({
      name: row.querySelector(".param-name").value,
      type: row.querySelector(".param-type").value,
      required: row.querySelector(".param-required").checked
    }))
  };
  const headers = { "Content-Type": "application/json", ...apiHeaders() };
  try {
    const response = await fetch("/api/tools", {
      method: "POST", headers, body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    result.textContent = `Created ${data.file}. Complete its handler, then restart MCP.`;
    form.reset();
    parameterList.replaceChildren();
  } catch (error) {
    result.textContent = error.message;
    result.className = "error";
  }
});
</script>
</body>
</html>
"""


class ToolBuilderError(ValueError):
    """An invalid tool definition supplied by the UI."""


def _builtin_tool_names() -> set[str]:
    names: set[str] = set(_KNOWN_BUILTIN_TOOLS)
    for source_path in BUNDLED_TOOLS_DIRECTORY.parent.glob("tools_*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            for decorator in getattr(node, "decorator_list", ()):
                if not isinstance(decorator, ast.Call):
                    continue
                function = decorator.func
                if not isinstance(function, ast.Attribute) or function.attr != "tool":
                    continue
                for item in decorator.keywords:
                    if (
                        item.arg == "name"
                        and isinstance(item.value, ast.Constant)
                        and isinstance(item.value.value, str)
                    ):
                        names.add(item.value.value)
    return names


def validate_tool_spec(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict) or set(spec) != {"name", "description", "parameters"}:
        raise ToolBuilderError("Expected exactly name, description, and parameters.")

    name = spec["name"]
    if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name) or keyword.iskeyword(name):
        raise ToolBuilderError("Tool name must be a lowercase Python-style identifier (1-64 characters).")
    if name in _builtin_tool_names():
        raise ToolBuilderError(f"Tool name '{name}' is already used by a built-in MCP tool.")

    description = spec["description"]
    if not isinstance(description, str) or not description.strip() or len(description) > 1000:
        raise ToolBuilderError("Description is required and must be 1-1000 characters.")
    try:
        description.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ToolBuilderError("Description must contain valid Unicode text.") from exc

    parameters = spec["parameters"]
    if not isinstance(parameters, list) or len(parameters) > MAX_PARAMETERS:
        raise ToolBuilderError(f"Parameters must be a list with at most {MAX_PARAMETERS} entries.")

    seen: set[str] = set()
    checked_parameters: list[dict[str, Any]] = []
    for parameter in parameters:
        if not isinstance(parameter, dict) or set(parameter) != {"name", "type", "required"}:
            raise ToolBuilderError("Each parameter must contain exactly name, type, and required.")
        param_name = parameter["name"]
        if (
            not isinstance(param_name, str)
            or not _IDENTIFIER.fullmatch(param_name)
            or keyword.iskeyword(param_name)
            or param_name in _RESERVED_PARAMETER_NAMES
        ):
            raise ToolBuilderError(f"Invalid or reserved parameter name: {param_name!r}.")
        if param_name in seen:
            raise ToolBuilderError(f"Duplicate parameter name: {param_name}.")
        seen.add(param_name)

        param_type = parameter["type"]
        if not isinstance(param_type, str) or param_type not in _PYTHON_TYPES:
            raise ToolBuilderError(f"Unsupported parameter type: {param_type!r}.")
        if not isinstance(parameter["required"], bool):
            raise ToolBuilderError(f"Parameter '{param_name}' must specify required as a boolean.")
        checked_parameters.append(
            {"name": param_name, "type": param_type, "required": parameter["required"]}
        )

    return {
        "name": name,
        "description": description.strip(),
        "parameters": checked_parameters,
    }


def generate_tool_module(spec: dict[str, Any]) -> str:
    parameters = sorted(spec["parameters"], key=lambda parameter: not parameter["required"])
    signature_parts = []
    for parameter in parameters:
        part = f"{parameter['name']}: {_PYTHON_TYPES[parameter['type']]}"
        if not parameter["required"]:
            part += f" = {_OPTIONAL_DEFAULTS[parameter['type']]}"
        signature_parts.append(part)
    signature = ", ".join(signature_parts)

    return (
        '"""Custom MCP tool scaffold generated by the Civil 3D Tool Builder."""\n\n'
        "from __future__ import annotations\n\n"
        "from typing import Any, Callable\n\n\n"
        "def register(mcp: Any, client: Any, run_com: Callable[..., Any]) -> None:\n"
        "    @mcp.tool(\n"
        f"        name={spec['name']!r},\n"
        f"        description={spec['description']!r},\n"
        "    )\n"
        f"    async def {spec['name']}({signature}) -> Any:\n"
        '        raise NotImplementedError("Complete this custom MCP tool handler before use.")\n'
    )


def save_tool_module(spec: dict[str, Any], tools_directory: Path | None = None) -> Path:
    tools_directory = tools_directory or user_tools_dir()
    checked = validate_tool_spec(spec)
    tools_directory.mkdir(parents=True, exist_ok=True)
    module_path = tools_directory / f"{checked['name']}.py"
    source = generate_tool_module(checked)
    try:
        with module_path.open("x", encoding="utf-8", newline="\n") as module_file:
            module_file.write(source)
    except FileExistsError as exc:
        raise FileExistsError(f"A custom tool named '{checked['name']}' already exists.") from exc
    except OSError:
        module_path.unlink(missing_ok=True)
        raise
    return module_path


class ToolBuilderHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[http.server.BaseHTTPRequestHandler],
        *,
        mode: str,
        access_token: str | None,
        tools_directory: Path,
        status_provider: Callable[[], dict[str, Any]] | None = None,
        claude_linker: Callable[[], list[Path]] | None = None,
    ) -> None:
        self.mode = mode
        self.access_token = access_token
        self.tools_directory = tools_directory
        self.status_provider = status_provider
        self.claude_linker = claude_linker
        super().__init__(server_address, handler_class)


def list_tool_files(tools_directory: Path) -> list[str]:
    if not tools_directory.is_dir():
        return []
    return sorted(p.stem for p in tools_directory.glob("*.py") if not p.name.startswith("_"))


class ToolBuilderRequestHandler(http.server.BaseHTTPRequestHandler):
    server: ToolBuilderHTTPServer

    def _authorized(self) -> bool:
        access_token = self.server.access_token
        if access_token is None:
            return True
        supplied = self.headers.get("Authorization", "").encode("utf-8")
        expected = f"Bearer {access_token}".encode("ascii")
        return hmac.compare_digest(supplied, expected)

    def do_GET(self) -> None:
        if self.path in {"/api/status", "/api/tools"}:
            if not self._authorized():
                self._send_json(401, {"error": "A valid LAN access token is required."})
                return
            if self.path == "/api/tools":
                self._send_json(200, {"tools": list_tool_files(self.server.tools_directory)})
                return
            status: dict[str, Any] = {"running": True, "civil3d_connected": False}
            if self.server.status_provider is not None:
                try:
                    status.update(self.server.status_provider())
                except Exception:
                    log.exception("Status provider failed")
            status["custom_tools"] = list_tool_files(self.server.tools_directory)
            status["tools_directory"] = str(self.server.tools_directory)
            self._send_json(200, status)
            return
        if self.path not in {"/", "/index.html"}:
            self._send_json(404, {"error": "Not found."})
            return
        page = _PAGE.replace("__MODE__", self.server.mode).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'",
        )
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self) -> None:
        if self.path not in {"/api/tools", "/api/claude/link"}:
            self._send_json(404, {"error": "Not found."})
            return

        if not self._authorized():
            self._send_json(401, {"error": "A valid LAN access token is required."})
            return

        if self.path == "/api/claude/link":
            self._link_claude()
            return

        if self.headers.get_content_type() != "application/json":
            self._send_json(415, {"error": "Content-Type must be application/json."})
            return
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send_json(411, {"error": "A valid Content-Length header is required."})
            return
        if content_length < 0 or content_length > MAX_REQUEST_BYTES:
            self._send_json(413, {"error": "Request body is too large."})
            return
        try:
            spec = json.loads(self.rfile.read(content_length))
            checked = validate_tool_spec(spec)
            module_path = save_tool_module(checked, self.server.tools_directory)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_json(400, {"error": f"Invalid JSON: {exc}"})
            return
        except ToolBuilderError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except FileExistsError as exc:
            self._send_json(409, {"error": str(exc)})
            return
        except OSError:
            log.exception("Could not save a generated MCP tool")
            self._send_json(500, {"error": "Could not save the generated tool. See the builder console."})
            return

        self._send_json(
            201,
            {
                "tool_name": checked["name"],
                "file": module_path.name,
                "message": "Complete the handler and restart the MCP server.",
            },
        )

    def _link_claude(self) -> None:
        linker = self.server.claude_linker
        if linker is None:
            self._send_json(501, {"error": "Claude linking is unavailable."})
            return
        try:
            updated = linker()
        except Exception as exc:
            log.exception("Claude link failed")
            self._send_json(500, {"error": str(exc)})
            return
        self._send_json(200, {"updated": [str(x) for x in updated]})

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        log.info("%s - %s", self.address_string(), format % args)


def create_server(
    mode: str = "localhost",
    port: int = 8765,
    tools_directory: Path | None = None,
    status_provider: Callable[[], dict[str, Any]] | None = None,
    claude_linker: Callable[[], list[Path]] | None = None,
) -> tuple[ToolBuilderHTTPServer, str | None]:
    if mode not in {"localhost", "lan"}:
        raise ValueError("mode must be 'localhost' or 'lan'.")
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535.")
    host = "127.0.0.1" if mode == "localhost" else "0.0.0.0"
    token = secrets.token_urlsafe(32) if mode == "lan" else None
    server = ToolBuilderHTTPServer(
        (host, port),
        ToolBuilderRequestHandler,
        mode=mode,
        access_token=token,
        tools_directory=tools_directory if tools_directory is not None else user_tools_dir(),
        status_provider=status_provider,
        claude_linker=claude_linker,
    )
    return server, token


def start_in_thread(
    mode: str = "localhost",
    port: int = 8765,
    **kwargs: Any,
) -> tuple[ToolBuilderHTTPServer, str | None]:
    server, token = create_server(mode, port, **kwargs)
    threading.Thread(target=server.serve_forever, name="tool-builder", daemon=True).start()
    return server, token


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Civil 3D MCP tool builder web UI.")
    parser.add_argument(
        "--mode",
        choices=("localhost", "lan"),
        default="localhost",
        help="Bind to this computer only (default) or to all network interfaces.",
    )
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765).")
    args = parser.parse_args()

    server, token = create_server(args.mode, args.port)
    host, port = server.server_address[:2]
    print(f"Tool Builder: http://{host if args.mode == 'localhost' else 'localhost'}:{port}")
    if token is not None:
        print(f"LAN access token: {token}")
        print(f"LAN clients can connect using this computer's LAN IP on port {port}.")
        print("Share this token only with trusted users on your network.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Tool Builder.")
    finally:
        server.server_close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
