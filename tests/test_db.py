from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from oilwatch.db import Database
from oilwatch.models import utcnow_naive


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.sqlite")
        self.db.init_schema()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _supplier(self, name: str, website: str, **overrides: object) -> dict:
        record = {
            "name": name,
            "website": website,
            "status": "active",
            "connector_type": "manual",
            "connector_config": {},
        }
        record.update(overrides)
        return record

    def test_upsert_creates_and_updates(self) -> None:
        supplier_id = self.db.upsert_supplier(self._supplier("A", "https://a.example.com"))
        self.assertGreater(supplier_id, 0)

        # Upserting the same website updates the existing row, not a new one.
        updated_id = self.db.upsert_supplier(
            self._supplier("A Renamed", "https://a.example.com", phone="01224 123456")
        )
        self.assertEqual(updated_id, supplier_id)

        suppliers = self.db.list_suppliers()
        self.assertEqual(len(suppliers), 1)
        self.assertEqual(suppliers[0]["name"], "A Renamed")
        self.assertEqual(suppliers[0]["phone"], "01224 123456")

    def test_record_quote_roundtrip(self) -> None:
        supplier_id = self.db.upsert_supplier(self._supplier("A", "https://a.example.com"))
        self.db.record_quote(
            {
                "supplier_id": supplier_id,
                "observed_at": "2026-03-20T10:00:00",
                "quantity_liters": 1000,
                "status": "ok",
                "price_per_liter": 0.71,
                "total_price": 710.0,
                "currency": "GBP",
                "source": "test",
                "notes": "",
                "raw_payload": {},
            }
        )
        self.assertEqual(len(self.db.all_quotes()), 1)

    def test_latest_quotes_uses_most_recent_ok_only(self) -> None:
        supplier_id = self.db.upsert_supplier(self._supplier("A", "https://a.example.com"))
        self.db.record_quote(
            {
                "supplier_id": supplier_id,
                "observed_at": "2026-03-19T10:00:00",
                "quantity_liters": 1000,
                "status": "ok",
                "price_per_liter": 0.80,
                "total_price": 800.0,
                "currency": "GBP",
                "source": "test",
                "notes": "",
                "raw_payload": {},
            }
        )
        self.db.record_quote(
            {
                "supplier_id": supplier_id,
                "observed_at": "2026-03-20T10:00:00",
                "quantity_liters": 1000,
                "status": "ok",
                "price_per_liter": 0.71,
                "total_price": 710.0,
                "currency": "GBP",
                "source": "test",
                "notes": "",
                "raw_payload": {},
            }
        )
        # A newer error row must not win over the last successful quote.
        self.db.record_quote(
            {
                "supplier_id": supplier_id,
                "observed_at": "2026-03-21T10:00:00",
                "quantity_liters": 1000,
                "status": "error",
                "price_per_liter": None,
                "total_price": None,
                "currency": "GBP",
                "source": "test",
                "notes": "",
                "raw_payload": {},
            }
        )
        latest = self.db.latest_quotes()
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["price_per_liter"], 0.71)

    def test_latest_quotes_max_age_excludes_stale_rows(self) -> None:
        stale_id = self.db.upsert_supplier(self._supplier("Stale", "https://stale.example.com"))
        fresh_id = self.db.upsert_supplier(self._supplier("Fresh", "https://fresh.example.com"))
        self.db.record_quote(
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
        self.db.record_quote(
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

        # Unbounded, the 2007 row is the cheapest thing in the database.
        self.assertEqual(
            [q["supplier_name"] for q in self.db.latest_quotes()], ["Stale", "Fresh"]
        )

        # With a recency window, the stale supplier drops out entirely.
        windowed = self.db.latest_quotes(max_age_days=30)
        self.assertEqual([q["supplier_name"] for q in windowed], ["Fresh"])

    def test_mark_missing_suppliers_inactive(self) -> None:
        self.db.upsert_supplier(self._supplier("A", "https://a.example.com"))
        self.db.upsert_supplier(self._supplier("B", "https://b.example.com"))
        self.db.mark_missing_suppliers_inactive(["https://a.example.com"])

        suppliers = self.db.list_suppliers(include_inactive=True)
        by_website = {s["website"]: s["status"] for s in suppliers}
        self.assertEqual(by_website["https://a.example.com"], "active")
        self.assertEqual(by_website["https://b.example.com"], "inactive")

    def test_connector_config_roundtrip(self) -> None:
        self.db.upsert_supplier(
            self._supplier(
                "A",
                "https://a.example.com",
                connector_type="price_page",
                connector_config={"quote_url": "https://a.example.com/prices"},
            )
        )
        supplier = self.db.list_suppliers()[0]
        self.assertEqual(supplier["connector_config"]["quote_url"], "https://a.example.com/prices")

    def test_record_brent_roundtrip_and_dedup(self) -> None:
        self.assertTrue(self.db.record_brent("2026-03-20", 70.5, "eia"))
        self.assertFalse(self.db.record_brent("2026-03-20", 71.0, "eia"))  # duplicate date ignored

        rows = self.db.all_brent()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["observed_at"], "2026-03-20")
        self.assertEqual(rows[0]["price_usd_per_barrel"], 70.5)
        self.assertEqual(rows[0]["source"], "eia")


if __name__ == "__main__":
    unittest.main()
