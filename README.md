# C3DMCP

MCP server điều khiển Autodesk Civil 3D qua COM (Windows).

## Yêu cầu

- Windows, Autodesk Civil 3D 2026 đang mở
- Python 3.11+

## Cài đặt

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

## Cấu hình MCP client (ví dụ VS Code `.vscode/mcp.json`)

```json
{
  "servers": {
    "civil3d-mcp": {
      "command": "F:\\path\\to\\C3DMCP\\.venv\\Scripts\\python.exe",
      "args": ["-m", "civil3d_mcp"]
    }
  }
}
```

Sửa đường dẫn theo máy của bạn.

## Kiểm thử

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

## Công cụ chính

Bản vẽ, điểm COGO, line/polyline, khối solid (`create_solid_box`, `create_solid_primitive`),
surface TIN, alignment, profile, corridor và các công cụ khám phá API Civil 3D.
