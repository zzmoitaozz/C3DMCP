from __future__ import annotations

import os
import unittest
from unittest import mock

from civil3d_mcp import server


class ToolsetTests(unittest.TestCase):
    def test_toolset_mode_defaults_to_full(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(server._toolset_mode(), "full")

    def test_toolset_mode_core(self) -> None:
        with mock.patch.dict(os.environ, {"CIVIL3D_TOOLSET": "core"}, clear=True):
            self.assertEqual(server._toolset_mode(), "core")

    def test_toolset_mode_invalid_falls_back_to_full(self) -> None:
        with mock.patch.dict(os.environ, {"CIVIL3D_TOOLSET": "x"}, clear=True):
            self.assertEqual(server._toolset_mode(), "full")

    def test_static_registrars_core_is_subset(self) -> None:
        core = server._static_registrars_for("core")
        full = server._static_registrars_for("full")
        self.assertEqual(len(core), 3)
        self.assertEqual(len(full), 6)
        self.assertTrue(set(core).issubset(set(full)))
