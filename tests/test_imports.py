from __future__ import annotations

import importlib
import pkgutil
import unittest

import oilwatch


class ModuleImportTests(unittest.TestCase):
    """Every oilwatch module must import cleanly.

    Catches wiring mistakes — a bad logger import, a renamed symbol — in modules
    that no test exercises directly. Several supplier connectors and the browser
    and registration helpers have no other coverage, so this is their only guard.
    """

    def test_every_module_imports(self) -> None:
        modules = sorted(m.name for m in pkgutil.walk_packages(oilwatch.__path__, "oilwatch."))
        self.assertTrue(modules, "no oilwatch modules discovered")

        failures = []
        for name in modules:
            try:
                importlib.import_module(name)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{name}: {type(exc).__name__}: {exc}")

        self.assertEqual(failures, [], "modules failed to import:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
