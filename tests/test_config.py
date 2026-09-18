from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oilwatch.config import load_settings, load_supplier_registry


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "config").mkdir(parents=True, exist_ok=True)
        (self.root / "data").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_settings(self) -> None:
        settings = {
            "database_path": "data/test.sqlite",
            "chart_path": "data/test.png",
            "home": {"label": "Home", "latitude": 57.2, "longitude": -2.2},
            "radius_miles": 50,
            "quote_quantity_liters": 1000,
            "currency": "GBP",
            "search_queries": ["heating oil Aberdeenshire"],
            "scheduler": {"discovery_interval_hours": 168, "quote_interval_hours": 24},
        }
        (self.root / "config" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    def _write_register(
        self, suppliers: list[dict[str, object]] | None = None, excluded: list[str] | None = None
    ) -> None:
        (self.root / "config" / "suppliers.json").write_text(
            json.dumps({"suppliers": suppliers or [], "excluded_domains": excluded or []}),
            encoding="utf-8",
        )

    def test_load_settings(self) -> None:
        self._write_settings()
        settings = load_settings(self.root)
        self.assertEqual(settings.home.label, "Home")
        self.assertEqual(settings.radius_miles, 50)
        self.assertEqual(settings.quote_quantity_liters, 1000)
        # Absent from the file, so the loader's own default applies.
        self.assertEqual(settings.quote_max_workers, 4)
        self.assertEqual(settings.search_queries, ["heating oil Aberdeenshire"])
        self.assertEqual(settings.scheduler.quote_interval_hours, 24)

    def test_the_domain_exclusions_come_from_the_register_not_settings(self) -> None:
        """Supplier policy is version controlled; settings.json is not.

        excluded_domains decides which companies count as suppliers, so it lives
        with the suppliers in the tracked register. settings.json is gitignored
        and holds the owner's personal values and operational settings — address,
        postcode, credentials, the freshness window, the order quantity — but no
        supplier policy, which is the distinction this checks.
        """
        self._write_settings()
        self._write_register(excluded=["example.com", "yell.com"])
        self.assertEqual(load_settings(self.root).excluded_domains, ["example.com", "yell.com"])

    def test_settings_default_to_the_checkout_root(self) -> None:
        """A process spawned elsewhere still reads this checkout's settings.

        The MCP server is launched by another program, so a default of
        ``Path.cwd()`` would read whichever directory it happened to inherit.
        """
        self._write_settings()
        with patch("oilwatch.config.CHECKOUT_ROOT", self.root):
            loaded = load_settings()
        self.assertEqual(loaded.home.label, "Home")

    def test_load_settings_reads_the_login_urls(self) -> None:
        settings = {
            "database_path": "data/test.sqlite",
            "chart_path": "data/test.png",
            "home": {"label": "Home", "latitude": 57.2, "longitude": -2.2},
            "login_urls": {"scottish_fuels": "https://quote.scottishfuels.co.uk/quote/"},
        }
        (self.root / "config" / "settings.json").write_text(
            json.dumps(settings), encoding="utf-8"
        )
        loaded = load_settings(self.root)
        self.assertEqual(
            loaded.login_urls, {"scottish_fuels": "https://quote.scottishfuels.co.uk/quote/"}
        )

    def test_the_login_urls_default_to_empty(self) -> None:
        self._write_settings()
        self.assertEqual(load_settings(self.root).login_urls, {})

    def test_load_supplier_registry_missing_file(self) -> None:
        self.assertEqual(load_supplier_registry(self.root), {"suppliers": [], "excluded_domains": []})

    def test_load_supplier_registry(self) -> None:
        suppliers = [{"name": "A", "website": "https://a.example.com"}]
        self._write_register(suppliers=suppliers, excluded=["yell.com"])
        self.assertEqual(
            load_supplier_registry(self.root),
            {"suppliers": suppliers, "excluded_domains": ["yell.com"]},
        )

    def test_a_register_that_is_not_an_object_is_refused(self) -> None:
        """A stale bare list must fail loudly, not read as no suppliers at all.

        Same reasoning as the settings drift check: the file's shape is part of
        its contract, and silently importing nothing looks like an install that
        has simply not been set up yet.
        """
        (self.root / "config" / "suppliers.json").write_text(
            json.dumps([{"name": "A", "website": "https://a.example.com"}]), encoding="utf-8"
        )
        with self.assertRaises(ValueError) as caught:
            load_supplier_registry(self.root)
        self.assertIn("bare list", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
