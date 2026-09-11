from __future__ import annotations

import importlib
import tomllib
import unittest
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import patch

import oilwatch

ROOT = Path(__file__).resolve().parents[1]


class VersionTests(unittest.TestCase):
    def test_the_package_version_matches_pyproject(self) -> None:
        """Two copies of a version drift apart, and a third made the handshake lie."""
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(oilwatch.__version__, config["project"]["version"])

    def test_running_from_source_is_reported_rather_than_crashing(self) -> None:
        with patch("oilwatch.version", side_effect=PackageNotFoundError("oilwatch")):
            self.assertEqual(oilwatch.package_version(), "0.0.0+source")


class EntryPointTests(unittest.TestCase):
    def test_console_scripts_resolve_to_callables(self) -> None:
        """Every ``[project.scripts]`` target must exist.

        A declared target that does not resolve is invisible until someone runs
        the installed ``.exe``, which then dies with ImportError. That happened
        to ``oilwatch-mcp``: it pointed at ``oilwatch.mcp_server:main`` before
        that function existed.
        """
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        scripts = config["project"]["scripts"]
        self.assertTrue(scripts, "no console scripts declared")

        for name, target in scripts.items():
            module_name, _, attribute = target.partition(":")
            with self.subTest(script=name):
                module = importlib.import_module(module_name)
                self.assertTrue(
                    callable(getattr(module, attribute, None)),
                    f"{name} -> {target} does not resolve to a callable",
                )


if __name__ == "__main__":
    unittest.main()
