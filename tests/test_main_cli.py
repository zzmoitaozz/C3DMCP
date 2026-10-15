from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

import civil3d_mcp.__main__ as cli_main


class MainCliTests(unittest.TestCase):
    def test_no_subcommand_defaults_to_serve(self) -> None:
        with patch.object(cli_main, "_serve") as mocked_serve, patch.object(
            sys, "argv", ["civil3d-mcp"]
        ):
            cli_main.main()
        mocked_serve.assert_called_once()

    def test_explicit_serve_passes_arguments(self) -> None:
        with patch.object(cli_main, "_serve") as mocked_serve, patch.object(
            sys,
            "argv",
            [
                "civil3d-mcp",
                "serve",
                "--web-port",
                "-1",
                "--web-mode",
                "lan",
                "--transport",
                "stdio",
                "--host",
                "127.0.0.1",
                "--port",
                "9900",
                "--allowed-host",
                "localhost",
            ],
        ):
            cli_main.main()
        mocked_serve.assert_called_once_with(
            -1,
            "stdio",
            "127.0.0.1",
            9900,
            None,
            None,
            ["localhost"],
            "lan",
        )


if __name__ == "__main__":
    unittest.main()