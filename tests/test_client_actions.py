from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from civil3d_mcp.client import Civil3DClient, Civil3DError


class FakeLayers:
    def __init__(self) -> None:
        self.names: list[str] = ["0"]

    def Item(self, name: str):
        if name not in self.names:
            raise KeyError("Key not found")
        return name

    def Add(self, name: str):
        self.names.append(name)
        return name


class FakeSolid:
    ObjectID = 42
    Handle = "2A"

    def __init__(self, layers: FakeLayers | None = None) -> None:
        self._layers = layers
        self._layer = "0"
        self.deleted = False

    @property
    def Layer(self) -> str:
        return self._layer

    @Layer.setter
    def Layer(self, value: str) -> None:
        if self._layers is not None:
            self._layers.Item(value)
        self._layer = value

    def Update(self) -> None:
        pass

    def Delete(self) -> None:
        self.deleted = True


class FakeDocument:
    Name = "site.dwg"
    FullName = r"C:\drawings\site.dwg"

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.Layers = FakeLayers()
        self.solid = FakeSolid(self.Layers)
        self.ModelSpace = self

    def GetVariable(self, name: str) -> int:
        return 0

    def AddBox(self, center, length, width, height):
        self.box_args = (center, length, width, height)
        return self.solid

    def SendCommand(self, command: str) -> None:
        self.commands.append(command)


class FakeApplication:
    def __init__(self, document: FakeDocument) -> None:
        self.ActiveDocument = document


class FakeEntity:
    def __init__(self, object_name: str, layer: str) -> None:
        self.ObjectName = object_name
        self.Layer = layer


class ClientActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = Civil3DClient()
        self.document = FakeDocument()
        self.client._acad = FakeApplication(self.document)
        self.client._connected = True

    def test_create_box_reports_dimensions_and_volume(self) -> None:
        with patch.object(
            Civil3DClient, "_pt3d", side_effect=lambda x, y, z: (x, y, z)
        ):
            result = self.client.create_solid_box(1, 2, 3, 4, 5, 6, "MASSING")

        self.assertEqual(result["volume"], 120.0)
        self.assertEqual(result["handle"], "2A")
        self.assertEqual(result["origin"], {"x": 1.0, "y": 2.0, "z": 3.0})
        self.assertEqual(self.document.solid.Layer, "MASSING")
        self.assertEqual(self.document.box_args, ((3.0, 4.5, 6.0), 4.0, 5.0, 6.0))

    def test_create_box_rejects_nonpositive_dimensions(self) -> None:
        with self.assertRaisesRegex(Civil3DError, "greater than zero"):
            self.client.create_solid_box(0, 0, 0, 1, 0, 1)

    def test_create_box_creates_missing_layer(self) -> None:
        with patch.object(
            Civil3DClient, "_pt3d", side_effect=lambda x, y, z: (x, y, z)
        ):
            self.client.create_solid_box(0, 0, 0, 1, 1, 1, "NEW_LAYER")

        self.assertIn("NEW_LAYER", self.document.Layers.names)

    def test_create_box_removes_entity_when_layer_assignment_fails(self) -> None:
        self.document.Layers.Add = lambda name: name  # layer can never be created

        with patch.object(
            Civil3DClient, "_pt3d", side_effect=lambda x, y, z: (x, y, z)
        ):
            with self.assertRaisesRegex(Civil3DError, "create_solid_box failed"):
                self.client.create_solid_box(0, 0, 0, 1, 1, 1, "MISSING")

        self.assertTrue(self.document.solid.deleted)

    def test_primitive_rejects_invalid_inputs(self) -> None:
        with self.assertRaisesRegex(Civil3DError, "inner_radius"):
            self.client.create_solid_primitive("cylinder", 0, 0, 0, radius=2, height=5, inner_radius=3)
        with self.assertRaisesRegex(Civil3DError, "kind must be"):
            self.client.create_solid_primitive("pyramid", 0, 0, 0)
        with self.assertRaisesRegex(Civil3DError, "points"):
            self.client.create_solid_primitive("revolve", 0, 0, 0, points=[[0, 0]])

    def test_axis_matrix_maps_z_to_direction(self) -> None:
        m = Civil3DClient._axis_matrix((1.0, 2.0, 3.0), (1.0, 0.0, 0.0))
        self.assertEqual([m[0][2], m[1][2], m[2][2]], [1.0, 0.0, 0.0])
        self.assertEqual([m[0][3], m[1][3], m[2][3]], [1.0, 2.0, 3.0])
        down = Civil3DClient._axis_matrix((0, 0, 0), (0, 0, -1))
        self.assertEqual(down[2][2], -1)

    def test_ensure_connected_reconnects_when_not_connected(self) -> None:
        self.client._connected = False
        with patch.object(Civil3DClient, "connect") as connect:
            self.client._ensure_connected()
        connect.assert_called_once()

    def test_checkpoint_restore_uses_latest_id_and_same_drawing(self) -> None:
        checkpoint = self.client.create_checkpoint()
        self.assertEqual(self.document.commands, ["_.UNDO _MARK\n"])

        with self.assertRaisesRegex(Civil3DError, "latest checkpoint"):
            self.client.restore_checkpoint("not-the-checkpoint")

        restored = self.client.restore_checkpoint(checkpoint["checkpoint_id"])
        self.assertTrue(restored["restored"])
        self.assertEqual(self.document.commands[-1], "_.UNDO _BACK\n")
        self.assertIsNone(self.client._checkpoint_id)

    def test_checkpoint_cannot_restore_in_a_different_drawing(self) -> None:
        checkpoint = self.client.create_checkpoint()
        self.document.FullName = r"C:\drawings\other.dwg"

        with self.assertRaisesRegex(Civil3DError, "differs"):
            self.client.restore_checkpoint(checkpoint["checkpoint_id"])

    def test_analyze_drawing_groups_model_space_entities_by_layer_and_type(self) -> None:
        self.document.ModelSpace = [
            FakeEntity("AcDbLine", "ROAD"),
            FakeEntity("AcDbLine", "road"),
            FakeEntity("AcDbPolyline", "BOUNDARY"),
        ]

        result = self.client.analyze_drawing()

        self.assertEqual(result["total_model_space_entities"], 3)
        self.assertEqual(
            result["entities_by_type"],
            {"AcDbLine": 2, "AcDbPolyline": 1},
        )
        self.assertEqual(
            result["layers"],
            [
                {
                    "name": "BOUNDARY",
                    "count": 1,
                    "entities_by_type": {"AcDbPolyline": 1},
                },
                {
                    "name": "ROAD",
                    "count": 2,
                    "entities_by_type": {"AcDbLine": 2},
                },
            ],
        )

    def test_analyze_drawing_surfaces_model_space_read_errors(self) -> None:
        class BrokenEntity:
            Layer = "ROAD"

            @property
            def ObjectName(self) -> str:
                raise RuntimeError("COM read failed")

        self.document.ModelSpace = [BrokenEntity()]

        with self.assertRaisesRegex(Civil3DError, "analyze_drawing failed"):
            self.client.analyze_drawing()

    def test_list_surfaces_returns_empty_when_civil_collection_is_empty(self) -> None:
        self.document.Surfaces = SimpleNamespace(Count=0)

        self.assertEqual(self.client.list_surfaces(), [])

    def test_list_alignments_returns_empty_when_civil_collections_are_empty(self) -> None:
        self.document.AlignmentsSiteless = SimpleNamespace(Count=0)
        self.document.Sites = SimpleNamespace(Count=0)

        self.assertEqual(self.client.list_alignments(), [])

    def test_empty_model_space_without_civil_collections_still_reports_unavailable(self) -> None:
        with self.assertRaisesRegex(Civil3DError, "collection not found"):
            self.client.list_surfaces()


if __name__ == "__main__":
    unittest.main()
