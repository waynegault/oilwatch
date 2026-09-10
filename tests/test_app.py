from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from oilwatch.models import utcnow_naive
from oilwatch.service import OilWatchApp


class OilWatchAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "config").mkdir(parents=True, exist_ok=True)
        (self.root / "data").mkdir(parents=True, exist_ok=True)
        settings = {
            "database_path": "data/test.sqlite",
            "chart_path": "data/test-chart.png",
            "home": {"label": "Hatton of Fintray, Aberdeenshire, Scotland", "latitude": 57.251, "longitude": -2.242},
            "radius_miles": 50,
            "quote_quantity_liters": 1000,
            "currency": "GBP",
            "search_queries": [],
            "excluded_domains": [],
            "scheduler": {"discovery_interval_hours": 168, "quote_interval_hours": 24}
        }
        (self.root / "config" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        self.app = OilWatchApp(self.root)
        self.app.init()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_imports_supplier_overrides(self) -> None:
        overrides = [
            {
                "name": "Test Supplier",
                "website": "https://example.com",
                "status": "active",
                "connector_type": "manual",
                "connector_config": {}
            }
        ]
        (self.root / "config" / "supplier_overrides.json").write_text(json.dumps(overrides), encoding="utf-8")
        result = self.app.init()
        self.assertEqual(result["imported_overrides"], 1)
        suppliers = self.app.suppliers()
        self.assertEqual(len(suppliers), 1)
        self.assertEqual(suppliers[0]["name"], "Test Supplier")

    def test_chart_builds_from_quote_history(self) -> None:
        self.app.db.upsert_supplier(
            {
                "name": "A",
                "website": "https://a.example.com",
                "status": "active",
                "connector_type": "manual",
                "connector_config": {}
            }
        )
        self.app.db.upsert_supplier(
            {
                "name": "B",
                "website": "https://b.example.com",
                "status": "active",
                "connector_type": "manual",
                "connector_config": {}
            }
        )
        suppliers = self.app.suppliers()
        ids = {supplier["name"]: supplier["id"] for supplier in suppliers}
        self.app.db.record_quote(
            {
                "supplier_id": ids["A"],
                "observed_at": "2026-03-20T10:00:00",
                "quantity_liters": 1000,
                "status": "ok",
                "price_per_liter": 0.71,
                "total_price": 710.0,
                "currency": "GBP",
                "source": "test",
                "notes": "",
                "raw_payload": {}
            }
        )
        self.app.db.record_quote(
            {
                "supplier_id": ids["B"],
                "observed_at": "2026-03-20T11:00:00",
                "quantity_liters": 1000,
                "status": "ok",
                "price_per_liter": 0.74,
                "total_price": 740.0,
                "currency": "GBP",
                "source": "test",
                "notes": "",
                "raw_payload": {}
            }
        )
        chart_path = Path(self.app.chart())
        self.assertTrue(chart_path.exists())

    def test_status_combines_snapshot_trend_and_recommendation(self) -> None:
        supplier_id = self.app.db.upsert_supplier(
            {
                "name": "A",
                "website": "https://a.example.com",
                "status": "active",
                "connector_type": "manual",
                "connector_config": {},
            }
        )
        self.app.db.record_quote(
            {
                "supplier_id": supplier_id,
                "observed_at": (utcnow_naive() - timedelta(days=1)).isoformat(),
                "quantity_liters": 1000,
                "status": "ok",
                "price_per_liter": 0.72,
                "total_price": 720.0,
                "currency": "GBP",
                "source": "test",
                "notes": "",
                "raw_payload": {},
            }
        )
        self.app.db.record_quote(
            {
                "supplier_id": supplier_id,
                "observed_at": utcnow_naive().isoformat(),
                "quantity_liters": 1000,
                "status": "ok",
                "price_per_liter": 0.70,
                "total_price": 700.0,
                "currency": "GBP",
                "source": "test",
                "notes": "",
                "raw_payload": {},
            }
        )

        status = self.app.status()
        self.assertEqual(status["market_snapshot"]["cheapest_supplier"]["name"], "A")
        self.assertEqual(status["trend"]["direction"], "falling")
        self.assertIn("falling", status["recommendation"])

    def test_cheapest_ignores_history_outside_the_age_window(self) -> None:
        stale_id = self.app.db.upsert_supplier(
            {
                "name": "Stale",
                "website": "https://stale.example.com",
                "status": "active",
                "connector_type": "manual",
                "connector_config": {},
            }
        )
        fresh_id = self.app.db.upsert_supplier(
            {
                "name": "Fresh",
                "website": "https://fresh.example.com",
                "status": "active",
                "connector_type": "manual",
                "connector_config": {},
            }
        )
        self.app.db.record_quote(
            {
                "supplier_id": stale_id,
                "observed_at": "2007-01-26",
                "quantity_liters": 1000,
                "status": "ok",
                "price_per_liter": 0.20,
                "total_price": 200.0,
                "currency": "GBP",
                "source": "spreadsheet",
                "notes": "",
                "raw_payload": {},
            }
        )
        self.app.db.record_quote(
            {
                "supplier_id": fresh_id,
                "observed_at": utcnow_naive().isoformat(),
                "quantity_liters": 1000,
                "status": "ok",
                "price_per_liter": 1.06,
                "total_price": 1060.0,
                "currency": "GBP",
                "source": "test",
                "notes": "",
                "raw_payload": {},
            }
        )

        # A 2007 spreadsheet row must not be reported as today's cheapest price.
        snapshot = self.app.cheapest()
        self.assertEqual(snapshot["cheapest_supplier"]["name"], "Fresh")
        self.assertEqual(snapshot["quotes_considered"], 1)


if __name__ == "__main__":
    unittest.main()
