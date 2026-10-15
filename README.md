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

## Điều khiển server từ GitHub Actions

Workflow `Civil 3D MCP server control` cho phép người có quyền chạy workflow trong
repo chọn `status`, `start` hoặc `stop` trên workstation Windows. Đây vẫn là MCP
HTTP server chạy trên máy cài Civil 3D; GitHub-hosted runner không thể điều khiển
Civil 3D trên máy cá nhân.

### Chuẩn bị một lần

1. Merge workflow này vào nhánh `main`.
2. Trên workstation Civil 3D, đăng ký GitHub Actions **self-hosted Windows x64 runner**
   cho repo và thêm custom label `civil3d`. Chạy runner trong phiên desktop của user
   đang mở Civil 3D; không cài runner dạng Windows service ở Session 0.
3. Tạo repository variable:
   - `CIVIL3D_MCP_REPOSITORY`: đường dẫn checkout local chứa source MCP.
   - `CIVIL3D_MCP_PYTHON`: Python trong virtualenv của source, ví dụ
     `F:\C3DMCP\.venv\Scripts\python.exe`.
   - `CIVIL3D_MCP_HOST`: mặc định dùng `127.0.0.1`; chỉ bind vào VPN/LAN khi đã
     cấu hình TLS và firewall phù hợp.
   - `CIVIL3D_MCP_PORT`: ví dụ `8766`.
   - Khi truy cập ngoài loopback, đặt `CIVIL3D_MCP_CERTFILE`,
     `CIVIL3D_MCP_KEYFILE` (đường dẫn file TLS trên runner) và
     `CIVIL3D_MCP_ALLOWED_HOST` (hostname mà client dùng).
4. Tạo repository secret `CIVIL3D_MCP_HTTP_TOKEN` với chuỗi ngẫu nhiên ít nhất
   32 ký tự. Không đưa bearer token hoặc private key vào Git.
5. Trên GitHub, vào **Actions → Civil 3D MCP server control → Run workflow**,
   chọn trạng thái cần kiểm tra hoặc hành động start/stop. Chỉ người được cấp
   quyền chạy workflow mới điều khiển được máy.

Server chỉ khởi chạy nếu `acad.exe` đang mở cùng phiên desktop với runner. Không
mở cổng MCP trực tiếp ra Internet; dùng VPN riêng hoặc reverse proxy TLS đã được
quản trị. Workflow chỉ quản lý tiến trình server MCP, không bật/tắt Civil 3D hay
khởi động máy tính.

## Công cụ chính

Bản vẽ, điểm COGO, line/polyline, khối solid (`create_solid_box`, `create_solid_primitive`),
surface TIN, alignment, profile, corridor và các công cụ khám phá API Civil 3D.
