from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from civil3d_mcp.tool_builder import (
    ToolBuilderError,
    ToolBuilderHTTPServer,
    ToolBuilderRequestHandler,
    create_server,
    generate_tool_module,
    save_tool_module,
    validate_tool_spec,
)

from civil3d_mcp.custom_tools import register_custom_tools


def tool_spec() -> dict[str, object]:
    return {
        "name": "get_parcel_area",
        "description": "Get a parcel's area",
        "parameters": [
            {"name": "include_holes", "type": "boolean", "required": False},
            {"name": "parcel_id", "type": "string", "required": True},
        ],
    }


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, *, name: str, description: str):
        def decorate(function):
            self.tools[name] = {"function": function, "description": description}
            return function

        return decorate


class ToolBuilderTests(unittest.TestCase):
    def test_validates_and_generates_typed_parameters(self) -> None:
        checked = validate_tool_spec(tool_spec())
        source = generate_tool_module(checked)

        self.assertLess(source.index("parcel_id: str"), source.index("include_holes: bool = False"))
        self.assertIn("name='get_parcel_area'", source)
        self.assertIn("raise NotImplementedError", source)
        compile(source, "get_parcel_area.py", "exec")

    def test_rejects_invalid_or_colliding_names_and_parameters(self) -> None:
        invalid_name = tool_spec()
        invalid_name["name"] = "get drawing info"
        with self.assertRaises(ToolBuilderError):
            validate_tool_spec(invalid_name)

        duplicate_name = tool_spec()
        duplicate_name["name"] = "get_drawing_info"
        with self.assertRaises(ToolBuilderError):
            validate_tool_spec(duplicate_name)

        generated_namespace = tool_spec()
        generated_namespace["name"] = "c3d_alignment_api"
        with self.assertRaises(ToolBuilderError):
            validate_tool_spec(generated_namespace)

        invalid_type = tool_spec()
        invalid_type["parameters"] = [{"name": "value", "type": [], "required": True}]
        with self.assertRaises(ToolBuilderError):
            validate_tool_spec(invalid_type)

        invalid_description = tool_spec()
        invalid_description["description"] = "\ud800"
        with self.assertRaises(ToolBuilderError):
            validate_tool_spec(invalid_description)

    def test_save_does_not_overwrite_existing_module(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = save_tool_module(tool_spec(), Path(directory))
            original = output.read_text(encoding="utf-8")

            with self.assertRaises(FileExistsError):
                save_tool_module(tool_spec(), Path(directory))

            self.assertEqual(output.read_text(encoding="utf-8"), original)

    def test_custom_module_registers_with_mcp_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tools_directory = Path(directory)
            save_tool_module(tool_spec(), tools_directory)
            mcp = FakeMCP()
            client = object()

            def run_com(function, *args):
                return function(*args)

            count = register_custom_tools(mcp, client, run_com, tools_directory)

            self.assertEqual(count, 1)
            self.assertIn("get_parcel_area", mcp.tools)

    def test_builder_runs_on_localhost_and_creates_tool_over_http(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server, token = create_server("localhost", 0, Path(directory))
            self.assertIsNone(token)
            self.assertEqual(server.server_address[0], "127.0.0.1")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                page = urlopen(f"http://127.0.0.1:{server.server_address[1]}/").read()
                self.assertIn(b'data-mode="localhost"', page)
                self.assertIn(b'<html lang="vi">', page)
                self.assertIn("Bạn muốn làm gì?".encode("utf-8"), page)
                self.assertIn("Sao chép câu gợi ý".encode("utf-8"), page)
                self.assertIn("create_cogo_point".encode("ascii"), page)
                self.assertIn("Nâng cao: Tạo công cụ MCP tùy chỉnh".encode("utf-8"), page)
                request = Request(
                    f"http://127.0.0.1:{server.server_address[1]}/api/tools",
                    data=json.dumps(tool_spec()).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request) as response:
                    payload = json.loads(response.read())
                    self.assertEqual(response.status, 201)
                    self.assertEqual(payload["file"], "get_parcel_area.py")
                self.assertTrue((Path(directory) / "get_parcel_area.py").is_file())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_lan_mode_requires_token_before_saving(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            server = ToolBuilderHTTPServer(
                ("127.0.0.1", 0),
                ToolBuilderRequestHandler,
                mode="lan",
                access_token="test-token",
                tools_directory=Path(directory),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_address[1]}/api/tools"
            try:
                payload = json.dumps(tool_spec()).encode("utf-8")
                request = Request(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(request)
                self.assertEqual(error.exception.code, 401)
                self.assertFalse((Path(directory) / "get_parcel_area.py").exists())

                invalid_token = Request(
                    url,
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer caf\u00e9",
                    },
                    method="POST",
                )
                with self.assertRaises(HTTPError) as invalid_error:
                    urlopen(invalid_token)
                self.assertEqual(invalid_error.exception.code, 401)

                authorized = Request(
                    url,
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer test-token",
                    },
                    method="POST",
                )
                with urlopen(authorized) as response:
                    self.assertEqual(response.status, 201)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
