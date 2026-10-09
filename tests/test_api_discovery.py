from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from fastmcp import FastMCP

from civil3d_mcp import api_discovery as disc
from civil3d_mcp import tools_api


class FakeSurface:
    def __init__(self, name: str) -> None:
        self.Name = name
        self.Area = 12.5
        self.Description = ""

    def Rebuild(self) -> bool:
        return True


class FakeDoc:
    Surfaces = [FakeSurface("EG"), FakeSurface("FG")]


class FakeClient:
    def _ensure_connected(self) -> None:
        pass

    def _civil_doc(self) -> FakeDoc:
        return FakeDoc()


async def run_com(fn, *a, **k):
    return fn(*a, **k)


def catalog():
    return disc.build_catalog([
        disc.InterfaceInfo("IAeccTinSurface", ["Name", "Area"], ["Description"], ["Rebuild", "Delete"]),
        disc.InterfaceInfo("IAeccAlignment", ["Length"], [], []),
        disc.InterfaceInfo("IAeccPipeNetwork", ["Name"], [], []),
        disc.InterfaceInfo("IAeccSettingsRoot", ["Name"], [], []),
    ])


class ClassifyTests(unittest.TestCase):
    def test_classify(self) -> None:
        cases = {
            "IAeccTinSurface": "surfaces",
            "IAeccAlignment": "alignments",
            "IAeccPipeNetwork": "pipe_networks",
            "IAeccPressurePipe": "pressure_networks",
            "IAeccSampleLine": "sample_lines",
            "IAeccCogoPoint": "cogo_points",
            "IAeccSettingsRoot": "styles",
            "IUnknownThing": "drawing",
        }
        for name, key in cases.items():
            self.assertEqual(disc.classify_interface(name), key, name)

    def test_members_exclude_blocked(self) -> None:
        m = disc.category_members(catalog(), "surfaces")
        self.assertIn("Rebuild", m["methods"])
        self.assertNotIn("Delete", m["methods"])


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mcp = FastMCP("t")
        self.count = tools_api.register(self.mcp, FakeClient(), run_com, catalog())

    def call(self, name, args):
        async def go():
            result = await self.mcp.call_tool(name, args)
            return result.structured_content
        return asyncio.run(go())

    def test_tool_count(self) -> None:
        self.assertEqual(self.count, 1 + 4 * len(disc.CATEGORIES))

    def test_list_get_invoke(self) -> None:
        self.assertEqual(self.call("c3d_surfaces_list", {})["objects"], ["EG", "FG"])
        got = self.call("c3d_surfaces_get", {"name": "EG"})
        self.assertEqual(got["properties"], {"Area": 12.5, "Name": "EG"})
        self.assertTrue(self.call("c3d_surfaces_invoke", {"name": "EG", "member": "Rebuild"})["result"])
        self.assertIn("error", self.call("c3d_surfaces_invoke", {"name": "EG", "member": "Delete"}))
        self.assertTrue(
            self.call("c3d_surfaces_invoke", {"name": "FG", "member": "Description", "set_value": "x"})["result"]
        )

    def test_categories(self) -> None:
        cats = self.call("c3d_api_categories", {})["categories"]
        self.assertEqual(next(c for c in cats if c["category"] == "surfaces")["methods"], 1)
