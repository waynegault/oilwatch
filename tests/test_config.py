from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from oilwatch.config import load_settings, load_supplier_overrides


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
            "excluded_domains": ["example.com"],
            "scheduler": {"discovery_interval_hours": 168, "quote_interval_hours": 24},
        }
        (self.root / "config" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    def test_load_settings(self) -> None:
        self._write_settings()
        settings = load_settings(self.root)
        self.assertEqual(settings.home.label, "Home")
        self.assertEqual(settings.radius_miles, 50)
        self.assertEqual(settings.quote_quantity_liters, 1000)
        self.assertEqual(settings.search_queries, ["heating oil Aberdeenshire"])
        self.assertEqual(settings.scheduler.quote_interval_hours, 24)

    def test_load_settings_reads_the_supplier_config(self) -> None:
        settings = {
            "database_path": "data/test.sqlite",
            "chart_path": "data/test.png",
            "home": {"label": "Home", "latitude": 57.2, "longitude": -2.2},
            "login_urls": {"scottish_fuels": "https://quote.scottishfuels.co.uk/quote/"},
            "submit_request_suppliers": ["gleaner_oils", "oilfast"],
        }
        (self.root / "config" / "settings.json").write_text(
            json.dumps(settings), encoding="utf-8"
        )
        loaded = load_settings(self.root)
        self.assertEqual(
            loaded.login_urls, {"scottish_fuels": "https://quote.scottishfuels.co.uk/quote/"}
        )
        self.assertEqual(loaded.submit_request_suppliers, ["gleaner_oils", "oilfast"])

    def test_the_supplier_config_defaults_to_empty(self) -> None:
        """Absent config means no default supplier list, not a code-baked one."""
        self._write_settings()
        loaded = load_settings(self.root)
        self.assertEqual(loaded.login_urls, {})
        self.assertEqual(loaded.submit_request_suppliers, [])

    def test_load_supplier_overrides_missing_file(self) -> None:
        self.assertEqual(load_supplier_overrides(self.root), [])

    def test_load_supplier_overrides(self) -> None:
        overrides = [{"name": "A", "website": "https://a.example.com"}]
        (self.root / "config" / "supplier_overrides.json").write_text(
            json.dumps(overrides), encoding="utf-8"
        )
        self.assertEqual(load_supplier_overrides(self.root), overrides)


if __name__ == "__main__":
    unittest.main()
