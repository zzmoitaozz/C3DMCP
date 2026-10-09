from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from civil3d_mcp.client import Civil3DClient, Civil3DError
from civil3d_mcp.tools_surfaces import register


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, *, name: str, description: str):
        def decorate(function):
            self.tools[name] = function
            return function

        return decorate


class FakeCollection:
    def __init__(self, items=()) -> None:
        self.items = list(items)
        self.Count = len(self.items)

    def Item(self, index: int):
        return self.items[index]

    def __iter__(self):
        return iter(self.items)


class SurfaceWorkflowTests(unittest.TestCase):
    def make_client(self, *, points, alignments, fail_profile=False):
        client = Civil3DClient()
        client._connected = True
        tin_data = SimpleNamespace()
        client._acad = SimpleNamespace(
            GetInterfaceObject=Mock(return_value=tin_data)
        )
        client._CIVIL3D_PROG_IDS = ["AeccXUiLand.AeccApplication.13.8"]

        group = SimpleNamespace(QueryBuilder=SimpleNamespace(), Delete=Mock())
        point_groups = SimpleNamespace(Add=Mock(return_value=group))
        profile_collection = FakeCollection()
        if fail_profile:
            profile_collection.AddFromSurface = Mock(
                side_effect=RuntimeError("profile creation failed")
            )
        else:
            profile_collection.AddFromSurface = Mock()

        alignment = SimpleNamespace(
            Name="Road A",
            StartingStation=0,
            EndingStation=100,
            Profiles=profile_collection,
        )
        surfaces_collection = FakeCollection()
        surface = SimpleNamespace(
            Name="EG_Surface",
            PointGroups=SimpleNamespace(Add=Mock()),
            Rebuild=Mock(),
            Delete=Mock(),
        )
        surfaces_collection.AddTinSurface = Mock(return_value=surface)
        doc = SimpleNamespace(
            Surfaces=surfaces_collection,
            PointGroups=point_groups,
            SurfaceStyles=FakeCollection([SimpleNamespace(Name="Existing Ground")]),
            ProfileStyles=FakeCollection([SimpleNamespace(Name="Standard")]),
            Layers=FakeCollection([SimpleNamespace(Name="0")]),
        )
        client._civil = SimpleNamespace(ActiveDocument=doc)
        client._doc = doc
        client._get_cogo_collection = Mock(return_value=FakeCollection(points))
        client._get_alignments = Mock(return_value=alignments or [alignment])
        return client, tin_data, group, point_groups, surfaces_collection, surface, profile_collection

    def test_creates_surface_from_exact_description_or_name_and_profiles(self) -> None:
        points = [
            SimpleNamespace(Number=4, RawDescription=" EG "),
            SimpleNamespace(Number=7, Name="eg"),
            SimpleNamespace(Number=9, RawDescription="EG ROAD"),
        ]
        client, tin_data, group, groups, surfaces, surface, profiles = self.make_client(
            points=points, alignments=None
        )

        result = client.create_surface_from_eg_cogo_points()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["matched_point_numbers"], [4, 7])
        self.assertEqual(group.QueryBuilder.IncludeNumbers, "4,7")
        groups.Add.assert_called_once()
        surfaces.AddTinSurface.assert_called_once_with(tin_data)
        surface.PointGroups.Add.assert_called_once_with(group)
        surface.Rebuild.assert_called_once_with()
        profiles.AddFromSurface.assert_called_once_with(
            "EG_Profile", 1, "Standard", "EG_Surface", 0.0, 100.0, "0"
        )

    def test_no_matching_points_does_not_create_group_or_surface(self) -> None:
        client, _, _, groups, surfaces, _, _ = self.make_client(
            points=[SimpleNamespace(Number=2, RawDescription="TOPO")],
            alignments=None,
        )

        with self.assertRaisesRegex(Civil3DError, "No COGO points"):
            client.create_surface_from_eg_cogo_points()

        groups.Add.assert_not_called()
        surfaces.AddTinSurface.assert_not_called()

    def test_reports_profile_failure_as_partial_result(self) -> None:
        client, _, _, _, _, _, profiles = self.make_client(
            points=[SimpleNamespace(Number=1, Name="EG")],
            alignments=None,
            fail_profile=True,
        )

        result = client.create_surface_from_eg_cogo_points()

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["profile_errors"], [{
            "alignment_name": "Road A",
            "error": "profile creation failed",
        }])
        self.assertFalse(result["profile_views_created"])
        profiles.AddFromSurface.assert_called_once()

    def test_registers_vietnamese_surface_management_tool(self) -> None:
        expected = {"status": "completed"}
        client = SimpleNamespace(
            create_surface_from_eg_cogo_points=Mock(return_value=expected)
        )
        mcp = FakeMCP()

        async def run_com(function, *args):
            return function(*args)

        register(mcp, client, run_com)
        result = asyncio.run(mcp.tools["tool_surface_mng"]())

        self.assertEqual(result, expected)
        client.create_surface_from_eg_cogo_points.assert_called_once_with(
            "EG_Surface", "EG_Profile", "EG"
        )


if __name__ == "__main__":
    unittest.main()
