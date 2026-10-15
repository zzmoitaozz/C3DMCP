from __future__ import annotations

import asyncio
import unittest

from civil3d_mcp.tools_drawing import register


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, *, name: str, description: str):
        def decorate(function):
            self.tools[name] = function
            return function

        return decorate


class FakeClient:
    def analyze_drawing(self) -> dict[str, object]:
        return {"total_model_space_entities": 0, "entities_by_type": {}, "layers": []}


async def fake_run_com(function):
    return function()


class DrawingToolTests(unittest.TestCase):
    def test_registers_and_returns_read_only_drawing_inventory(self) -> None:
        mcp = FakeMCP()
        register(mcp, FakeClient(), fake_run_com)

        result = asyncio.run(mcp.tools["analyze_drawing"]())

        self.assertEqual(
            result,
            {"total_model_space_entities": 0, "entities_by_type": {}, "layers": []},
        )


if __name__ == "__main__":
    unittest.main()
