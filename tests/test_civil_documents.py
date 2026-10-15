from __future__ import annotations

import unittest
from types import SimpleNamespace

from civil3d_mcp import api_discovery as disc
from civil3d_mcp.client import _CivilDocuments


class CivilDocumentsTests(unittest.TestCase):
    def test_resolves_across_documents(self) -> None:
        land = SimpleNamespace(Surfaces=["s"])
        road = SimpleNamespace(Corridors=["c"])
        pipe = SimpleNamespace(PipeNetworks=["p"])
        doc = _CivilDocuments(land, [road, pipe])
        self.assertEqual(doc.Surfaces, ["s"])
        self.assertEqual(doc.Corridors, ["c"])
        self.assertEqual(doc.PipeNetworks, ["p"])

    def test_missing_attribute_raises(self) -> None:
        doc = _CivilDocuments(SimpleNamespace(), [SimpleNamespace()])
        with self.assertRaises(AttributeError):
            doc.Nothing

    def test_primary_wins(self) -> None:
        doc = _CivilDocuments(SimpleNamespace(X=1), [SimpleNamespace(X=2)])
        self.assertEqual(doc.X, 1)


class CategoryPathTests(unittest.TestCase):
    def test_profiles_use_siteless_alignments(self) -> None:
        text = repr(disc.__dict__)
        self.assertIn("AlignmentsSiteless", text)
        self.assertNotIn('("Alignments", "Profiles")', text)


if __name__ == "__main__":
    unittest.main()
