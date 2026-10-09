
---

## Requirements

| Requirement | Notes |
|---|---|
| Windows 10/11 | COM automation is Windows-only |
| Python 3.11 | 64-bit recommended |
| Autodesk Civil 3D | 2023, 2024, 2025 or 2026 — must be **open with a drawing loaded** |
| pip packages | `fastmcp`, `pythonnet`, `pywin32`, `pydantic` |

---

## Installation

```bash
# 1. Clone
git clone https://github.com/zzmoitaozz/C3DMCP
cd C3DMCP

# 2. Install dependencies (Windows, Python 3.11)
pip install -r requirements.txt

# 3. Install the package in editable mode
pip install -e .

# 4. Run the pre-flight environment checker
py -3.11-64 setup_check.py
# Use --fix to auto-install any missing pip packages:
py -3.11-64 setup_check.py --fix

# 5. (Optional) copy environment config
copy .env.example .env
# Edit .env if Civil 3D is not in the default installation path
```

---

## Configuration

### Claude Desktop

Install this package in the Python environment used by Claude Desktop, then merge
the valid JSON in `claude_desktop_config_snippet.json` into
`%APPDATA%\Claude\claude_desktop_config.json`. If `civil3d-mcp` is not on
Claude Desktop's `PATH`, replace `command` with the full path to the executable
or use `python` with `-m civil3d_mcp` from the project environment. Start Civil
3D and open a drawing before restarting Claude Desktop.

The local stdio setup is the supported default. The MCP server attaches to
Civil 3D through Windows COM, so it must run on the **same Windows computer and
interactive user session** as Civil 3D. A Claude Desktop client on another
computer cannot directly automate its own Civil 3D process through this local
stdio configuration.

### Remote MCP client

Remote use runs the MCP server on the Windows host where Civil 3D is open, then
connects a remote MCP client to its Streamable HTTP endpoint (`/mcp`). This
project's HTTP mode requires a high-entropy bearer token; binding beyond
localhost additionally requires TLS certificate/key files and at least one
allowed host. Example on the Civil 3D host:

```powershell
$env:CIVIL3D_MCP_HTTP_TOKEN = "<at-least-32-random-characters>"
civil3d-mcp serve --transport http --host 0.0.0.0 --port 8766 `
  --ssl-certfile "C:\certs\civil3d-mcp.crt" `
  --ssl-keyfile "C:\certs\civil3d-mcp.key" `
  --allowed-host "civil3d-host.example.com" --web-port -1
```

The remote client must support Streamable HTTP and sending the matching
`Authorization: Bearer ...` header. This repo's `install-claude` helper only
installs the local stdio entry; it does not configure a remote Claude Desktop
connection. Verify the exact remote-server and custom-header setup supported by
the installed Claude Desktop version before relying on remote access. Do not
expose the endpoint to the public internet; use a trusted private network or
VPN, firewall rules, TLS, and a private token. Network connectivity alone does
not make Windows COM control Civil 3D on the client computer.

Restart Claude Desktop after configuration. Its MCP connection indicator
confirms a server connection, but does not prove Civil 3D is available until a
tool call succeeds.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `CIVIL3D_BIN_PATH` | Auto-detected | Path to folder containing `AeccDbMgd.dll` |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `CIVIL3D_MCP_TRANSPORT` | `stdio` | `stdio` or `http` |
| `CIVIL3D_MCP_HOST` | `127.0.0.1` | HTTP bind address |
| `CIVIL3D_MCP_PORT` | `8766` | HTTP port |
| `CIVIL3D_MCP_HTTP_TOKEN` | — | Required bearer token for HTTP (minimum 32 characters) |

---

## Available tools

### Drawing
| Tool | Description |
|---|---|
| `get_drawing_info` | Drawing name, path, save state, unit settings |
| `list_civil_object_types` | Count objects by type in model space |
| `get_selected_objects_info` | Properties of currently selected objects |

### COGO Points
| Tool | Description |
|---|---|
| `create_cogo_point` | Create a point at (northing, easting, elevation) |
| `list_cogo_points` | List all COGO points |
| `delete_cogo_point` | Delete a point by point number |

### Lines & Polylines
| Tool | Description |
|---|---|
| `create_line` | Create a 3D line from two points |
| `create_polyline` | Create a 3D polyline from a vertex list |
| `list_lines` | List all lines/polylines, optionally by layer |
| `create_solid_box` | Create an axis-aligned 3D box solid from a lower corner and positive dimensions |
| `create_checkpoint` | Mark the active drawing's current undo history |
| `restore_checkpoint` | Undo changes back to the latest checkpoint in this server session |

Checkpoints are in-memory session identifiers backed by AutoCAD's undo history:
they are not saved files, survive neither server restarts nor drawing changes,
and only the latest checkpoint can be restored. Restoring undoes drawing edits;
use it deliberately. Live command-queue behavior still depends on Civil 3D's
`SendCommand`/UNDO implementation.

### Surfaces
| Tool | Description |
|---|---|
| `list_surfaces` | List all TIN/Grid surfaces with elevation stats |
| `get_surface_info` | Detailed stats: point/triangle count, 2D/3D area |
| `sample_surface_elevation` | Sample elevation at (easting, northing) |
| `list_surface_definition` | List all definition items (boundaries, breaklines, contours, DEM files, etc.) |

### Alignments & Profiles
| Tool | Description |
|---|---|
| `list_alignments` | List all alignments with station range and length |
| `get_alignment_info` | Geometry breakdown: tangents, curves, spirals |
| `get_station_offset` | Project a world point to station + offset |
| `list_profiles` | List all profiles attached to an alignment |
| `get_profile_info` | Detailed info for a named profile |

### Corridors
| Tool | Description |
|---|---|
| `get_corridor_info` | Baselines, regions, and assembly tree for a corridor |

---

## Example prompts

```
"What surfaces are in this drawing and what are their elevation ranges?"

"Create a COGO point at northing 1000, easting 500, elevation 12.5 with description 'BM-01'"

"What is the elevation of the EG surface at easting 45200, northing 87300?"

"List all alignments and give me the geometry breakdown of CL-MAIN"

"What is the station and offset of point (45300, 87450) relative to alignment CL-MAIN?"

"Create a polyline on layer BOUNDARY with vertices at (0,0,0), (100,0,0), (100,100,0), (0,100,0) and close it"

"Create a box solid at (100, 200, 0) with length 10, width 6, and height 3 on layer MASSING"

"Create a checkpoint before modifying the drawing"
```

These tools do not infer terrain extents from arbitrary drawing entities and do
not create TIN surfaces automatically. A box solid is geometric drawing
content, not a terrain model.

### Discovered Civil 3D API tools

The server also registers `c3d_api_categories` and four generated tools for
each API category: `c3d_<category>_api`, `_list`, `_get`, and `_invoke`.
Categories currently cover alignments, corridors, COGO points, drawing,
feature lines, parcels, pipe and pressure networks, profiles, sample lines,
styles, surfaces, and survey. Discovery reads Civil 3D type libraries on the
machine running the server; the generated tools are present even when a local
type library scan yields no members, but their catalog will then be empty.

---

## Project structure

```
C3DMCP/
├── src/
│   └── civil3d_mcp/
│       ├── client.py             # Civil 3D COM/pythonnet operations
│       ├── server.py             # MCP server and tool registration
│       ├── tools_api.py          # Generated Civil 3D API tools
│       ├── api_discovery.py      # Civil 3D API discovery
│       ├── tool_builder.py       # Custom tool management
│       └── tools_*.py            # Drawing, COGO, geometry, and Civil 3D tools
├── tests/
│   └── test_*.py                 # Mocked COM and tool tests
├── setup_check.py                # Pre-flight environment checker
├── pyproject.toml                # PEP 517/518 build config
├── requirements.txt              # Runtime dependencies
├── requirements-dev.txt          # Dev/test dependencies
├── .env.example                  # Environment variable template
├── claude_desktop_config_snippet.json
└── SkillC3D.md                   # Setup, deployment, and tool documentation
```

---

## Running tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

Tests use a fully mocked `Civil3DClient` — no Civil 3D installation required to run the test suite.

---

## Troubleshooting

**"Could not connect to a running Civil 3D instance"**  
→ Make sure Civil 3D is open and a drawing is loaded before starting the MCP server.
→ Use a 64-bit Python interpreter; on Windows this is usually `py -3.11-64 -m civil3d_mcp.server`.

**"Civil 3D .NET assemblies not found"**  
→ Set `CIVIL3D_BIN_PATH` in `.env` to the folder containing `AeccDbMgd.dll`  
→ Default paths include the Civil 3D folders for AutoCAD 2023–2026; set
  `CIVIL3D_BIN_PATH` to the directory containing `AeccDbMgd.dll` if needed.

**"Surfaces / Alignments collection not accessible"**  
→ Ensure you launched Civil 3D (not plain AutoCAD). The server falls back to AutoCAD's ProgID if the Civil 3D ProgID isn't registered.

**Hammer icon not showing in Claude Desktop**  
→ Check Claude Desktop logs: `%APPDATA%\Claude\logs\`  
→ Run `python -m civil3d_mcp.server` manually to see startup errors.

---

## Custom MCP tool builder

Start the builder from the project environment:

```powershell
python -m civil3d_mcp.tool_builder
```

By default, the web UI binds only to this computer at
`http://127.0.0.1:8765`. To allow trusted users on the LAN to reach it, opt in
explicitly:

```powershell
python -m civil3d_mcp.tool_builder --mode lan --port 8765
```

LAN mode prints a generated access token to the builder console. Enter it in
the UI before creating a tool; share it only with trusted users. The builder
accepts tool names, descriptions, and up to 20 typed parameters (string,
integer, number, or boolean). It writes a Python scaffold under
`src/civil3d_mcp/custom_tools/` and will not overwrite an existing module.

Generated handlers intentionally raise `NotImplementedError`. Edit the new
module and implement its handler using the provided `client` and `run_com`
arguments before use. The MCP server imports custom modules on startup, so
restart the MCP server after editing or creating a tool. Only run custom
modules whose code you trust.

## Single-exe packaging

- Build: `.\build_exe.ps1` (set `BUILD_PYTHON` to a Python 3.11+) -> `dist\civil3d-mcp.exe`.
- `civil3d-mcp.exe serve` (default): stdio MCP for Claude Desktop plus Web UI at http://localhost:8765 (status + tool builder).
- `install-claude` merges the exe into claude_desktop_config.json; `install-autocad` copies it to an AutoCAD ApplicationPlugins bundle; `web` runs the UI alone.
- Custom tools are saved to `%APPDATA%\Civil3DMCP\custom_tools` (override: `CIVIL3D_MCP_TOOLS_DIR`); restart Claude Desktop to load new tools.
