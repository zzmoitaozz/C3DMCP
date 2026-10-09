from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from civil3d_mcp.client import Civil3DClient, Civil3DError
from civil3d_mcp.tools_cogo import register


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, *, name: str, description: str):
        def decorate(function):
            self.tools[name] = function
            return function

        return decorate


class CogoCollectionTests(unittest.TestCase):
    def test_finds_points_collection(self) -> None:
        collection = SimpleNamespace(Count=0)
        client = Civil3DClient()
        client._doc = SimpleNamespace(Points=collection)

        self.assertIs(client._get_cogo_collection(), collection)

    def test_finds_legacy_cogopoints_collection(self) -> None:
        collection = SimpleNamespace(Count=0)
        client = Civil3DClient()
        client._doc = SimpleNamespace(CogoPoints=collection)

        self.assertIs(client._get_cogo_collection(), collection)

    def test_continues_after_com_collection_lookup_failure(self) -> None:
        class BrokenDocument:
            @property
            def Points(self):
                raise RuntimeError("COM dispatch failed")

            @property
            def CogoPoints(self):
                raise RuntimeError("COM dispatch failed")

        collection = SimpleNamespace(Count=0)
        client = Civil3DClient()
        client._doc = BrokenDocument()
        client._civil = SimpleNamespace(
            ActiveDocument=SimpleNamespace(Points=collection)
        )

        self.assertIs(client._get_cogo_collection(), collection)

    def test_reports_missing_collection(self) -> None:
        client = Civil3DClient()
        client._doc = SimpleNamespace()

        with self.assertRaisesRegex(Civil3DError, "Points and CogoPoints"):
            client._get_cogo_collection()


class CreateCogoPointToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = SimpleNamespace(create_cogo_point=Mock(return_value={"point_number": 1}))
        self.mcp = FakeMCP()
        register(self.mcp, self.client, self.run_com)
        self.create = self.mcp.tools["create_cogo_point"]

    @staticmethod
    async def run_com(function, *args, **kwargs):
        return function(*args, **kwargs)

    def test_accepts_legacy_easting_northing_payload(self) -> None:
        result = asyncio.run(self.create(
            easting=0, northing=0, elevation=0, description="CHAN COT"
        ))

        self.assertEqual(result, {"point_number": 1})
        self.client.create_cogo_point.assert_called_once_with(
            easting=0, northing=0, elevation=0.0, description="CHAN COT"
        )

    def test_accepts_xy_alias_payload(self) -> None:
        result = asyncio.run(self.create(X=12, Y=34, Z=5))

        self.assertEqual(result, {"point_number": 1})
        self.client.create_cogo_point.assert_called_once_with(
            easting=12, northing=34, elevation=5, description=""
        )

    def test_rejects_missing_or_ambiguous_coordinates(self) -> None:
        missing = asyncio.run(self.create())
        ambiguous = asyncio.run(self.create(easting=1, northing=2, X=1))

        self.assertIn("Provide easting and northing", missing["error"])
        self.assertIn("either easting or X", ambiguous["error"])
        self.client.create_cogo_point.assert_not_called()


if __name__ == "__main__":
    unittest.main()
