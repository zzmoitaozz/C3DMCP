from __future__ import annotations

import unittest
from unittest.mock import patch

from civil3d_mcp.client import Civil3DClient, Civil3DError


class FakeSolid:
    ObjectID = 42
    Handle = "2A"
    Layer = "0"

    def Update(self) -> None:
        pass


class FakeDocument:
    Name = "site.dwg"
    FullName = r"C:\drawings\site.dwg"

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.solid = FakeSolid()
        self.ModelSpace = self

    def AddBox(self, origin, length, width, height):
        self.box_args = (origin, length, width, height)
        return self.solid

    def SendCommand(self, command: str) -> None:
        self.commands.append(command)


class FakeApplication:
    def __init__(self, document: FakeDocument) -> None:
        self.ActiveDocument = document


class ClientActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = Civil3DClient()
        self.document = FakeDocument()
        self.client._acad = FakeApplication(self.document)
        self.client._connected = True

    def test_create_box_reports_dimensions_and_volume(self) -> None:
        with patch.object(Civil3DClient, "_pt3d", return_value=(1, 2, 3)):
            result = self.client.create_solid_box(1, 2, 3, 4, 5, 6, "MASSING")

        self.assertEqual(result["volume"], 120.0)
        self.assertEqual(result["handle"], "2A")
        self.assertEqual(self.document.solid.Layer, "MASSING")
        self.assertEqual(self.document.box_args, ((1, 2, 3), 4.0, 5.0, 6.0))

    def test_create_box_rejects_nonpositive_dimensions(self) -> None:
        with self.assertRaisesRegex(Civil3DError, "greater than zero"):
            self.client.create_solid_box(0, 0, 0, 1, 0, 1)

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


if __name__ == "__main__":
    unittest.main()
