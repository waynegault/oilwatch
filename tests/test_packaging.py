from __future__ import annotations

import importlib
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
