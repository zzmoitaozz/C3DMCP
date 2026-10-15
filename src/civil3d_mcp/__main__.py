"""Entry point: `civil3d-mcp.exe [serve|web|install-claude|install-autocad]`."""

from __future__ import annotations

import argparse
import logging
import os
import sys

DEFAULT_PORT = 8765


def _serve(
    web_port: int,
    transport: str,
    host: str,
    port: int,
    ssl_certfile: str | None,
    ssl_keyfile: str | None,
    allowed_hosts: list[str],
    web_mode: str = "localhost",
) -> None:
    from civil3d_mcp import server
    from civil3d_mcp.integration import link_claude_desktop
    from civil3d_mcp.paths import user_tools_dir
    from civil3d_mcp.tool_builder import _builtin_tool_names, start_in_thread

    def status() -> dict:
        return {
            "civil3d_connected": bool(getattr(server.client, "_connected", False)),
            "tool_execution_enabled": server.tool_execution_enabled(),
            "static_tool_count": len(_builtin_tool_names()),
            "generated_api_tool_count": server.generated_api_tool_count,
            "custom_tool_module_count": server.custom_tool_modules,
            "executable": sys.executable,
        }

    if web_port >= 0:
        try:
            web, web_token = start_in_thread(
                web_mode,
                web_port,
                tools_directory=user_tools_dir(),
                status_provider=status,
                claude_linker=link_claude_desktop,
                tool_execution_controller=server.set_tool_execution_enabled,
            )
            logging.getLogger("civil3d_mcp").info(
                "Tool Builder UI: http://%s:%s",
                "localhost" if web_mode == "localhost" else "<computer-LAN-IP>",
                web.server_address[1],
            )
            if web_token:
                logging.getLogger("civil3d_mcp").info(
                    "Tool Builder LAN access token: %s", web_token
                )
        except OSError as exc:
            logging.getLogger("civil3d_mcp").warning("Web UI disabled: %s", exc)
    server.main(
        transport=transport,
        host=host,
        port=port,
        token=os.environ.get("CIVIL3D_MCP_HTTP_TOKEN"),
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
        allowed_hosts=allowed_hosts,
    )


def _web(mode: str, port: int) -> None:
    from civil3d_mcp import tool_builder
    from civil3d_mcp.integration import link_claude_desktop

    srv, token = tool_builder.create_server(mode, port, claude_linker=link_claude_desktop)
    print(f"Tool Builder: http://localhost:{srv.server_address[1]}")
    if token:
        print(f"LAN access token: {token}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


def main() -> None:
    default_web_port = int(os.environ.get("CIVIL3D_MCP_WEB_PORT", DEFAULT_PORT))
    default_transport = os.environ.get("CIVIL3D_MCP_TRANSPORT", "stdio")
    default_host = os.environ.get("CIVIL3D_MCP_HOST", "127.0.0.1")
    default_http_port = int(os.environ.get("CIVIL3D_MCP_PORT", "8766"))

    parser = argparse.ArgumentParser(prog="civil3d-mcp")
    sub = parser.add_subparsers(dest="command")
    p_serve = sub.add_parser("serve", help="Run the MCP server over stdio (default).")
    p_serve.add_argument(
        "--web-port",
        type=int,
        default=default_web_port,
        help="Web UI port (-1 disables it).",
    )
    p_serve.add_argument(
        "--web-mode",
        choices=("localhost", "lan"),
        default="localhost",
        help="Bind the Tool Builder to localhost (default) or the LAN.",
    )
    p_serve.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default=default_transport,
        help="MCP transport. Remote HTTP requires a bearer token and TLS off-loopback.",
    )
    p_serve.add_argument(
        "--host",
        default=default_host,
        help="HTTP bind address; use a private network interface for remote clients.",
    )
    p_serve.add_argument(
        "--port",
        type=int,
        default=default_http_port,
        help="HTTP transport port.",
    )
    p_serve.add_argument("--ssl-certfile", help="TLS certificate file for HTTP transport.")
    p_serve.add_argument("--ssl-keyfile", help="TLS private key file for HTTP transport.")
    p_serve.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        help="Allowed HTTP Host header (repeat for each hostname used by clients).",
    )
    p_web = sub.add_parser("web", help="Run only the web UI.")
    p_web.add_argument("--mode", choices=("localhost", "lan"), default="localhost")
    p_web.add_argument("--port", type=int, default=DEFAULT_PORT)
    sub.add_parser("install-claude", help="Add this exe to claude_desktop_config.json.")
    sub.add_parser("install-autocad", help="Copy this exe into an AutoCAD ApplicationPlugins bundle.")
    args = parser.parse_args()

    command = args.command or "serve"
    if command == "serve":
        _serve(
            getattr(args, "web_port", default_web_port),
            getattr(args, "transport", default_transport),
            getattr(args, "host", default_host),
            getattr(args, "port", default_http_port),
            getattr(args, "ssl_certfile", None),
            getattr(args, "ssl_keyfile", None),
            getattr(args, "allowed_host", []),
            getattr(args, "web_mode", "localhost"),
        )
    elif command == "web":
        logging.basicConfig(level=logging.INFO)
        _web(args.mode, args.port)
    elif command == "install-claude":
        from civil3d_mcp.integration import link_claude_desktop

        updated = link_claude_desktop()
        print("\n".join(str(p) for p in updated) or "No Claude Desktop config folder found.")
    elif command == "install-autocad":
        from civil3d_mcp.integration import install_autocad_bundle

        print(install_autocad_bundle())


if __name__ == "__main__":
    main()
