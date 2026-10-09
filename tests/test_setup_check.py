from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import setup_check


class SetupCheckTests(unittest.TestCase):
    def test_finds_autodesk_dlls_split_between_civil3d_and_autocad_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            autocad_root = Path(directory)
            civil3d_root = autocad_root / "C3D"
            civil3d_root.mkdir()
            (civil3d_root / "AeccDbMgd.dll").touch()
            (autocad_root / "acdbmgd.dll").touch()

            with patch.dict(os.environ, {"CIVIL3D_BIN_PATH": str(civil3d_root)}):
                result = setup_check.check_autodesk_dlls()

            self.assertTrue(result.passed, result.detail)
            self.assertIn(str(civil3d_root / "AeccDbMgd.dll"), result.detail)
            self.assertIn(str(autocad_root / "acdbmgd.dll"), result.detail)


if __name__ == "__main__":
    unittest.main()
