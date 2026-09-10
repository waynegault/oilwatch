from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from oilwatch.analytics import AnalyticsService
from oilwatch.db import Database
from oilwatch.models import QuoteResult

OBSERVED = datetime(2026, 9, 10, 12, 0, 0)


def _result(**overrides) -> QuoteResult:
    payload = {
        "supplier_id": 1,
        "supplier_name": "A",
        "observed_at": OBSERVED,
        "quantity_liters": 1000,
        "status": "ok",
        "price_per_liter": 1.0,
        "total_price": 1000.0,
    }
    payload.update(overrides)
    return QuoteResult(**payload)


class ValidityDefaultsTests(unittest.TestCase):
    def test_every_quote_record_carries_a_valid_until(self) -> None:
        record = _result().to_record()
        self.assertEqual(record["valid_until"], (OBSERVED + timedelta(hours=24)).isoformat())

    def test_a_connector_supplied_validity_wins(self) -> None:
        explicit = datetime(2026, 9, 17, 9, 0, 0)
        record = _result(valid_until=explicit).to_record()
        self.assertEqual(record["valid_until"], explicit.isoformat())

    def test_applies_to_non_ok_quotes_too(self) -> None:
        """A failed quote still gets one, so the field is never absent."""
        record = _result(status="error", price_per_liter=None, total_price=None).to_record()
        self.assertTrue(record["valid_until"])


class ValidityPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.sqlite")
        self.db.init_schema()
        self.supplier_id = self.db.upsert_supplier(
            {
                "name": "A",
                "website": "https://a.example.com",
                "status": "active",
                "connector_type": "manual",
                "connector_config": {},
            }
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_round_trips_through_the_database(self) -> None:
        valid_until = datetime(2026, 9, 11, 12, 0, 0)
        self.db.record_quote(
            {
                "supplier_id": self.supplier_id,
                "observed_at": OBSERVED.isoformat(),
                "quantity_liters": 1000,
                "status": "ok",
                "price_per_liter": 1.0,
                "total_price": 1000.0,
                "currency": "GBP",
                "source": "test",
                "notes": "",
                "valid_until": valid_until.isoformat(),
                "raw_payload": {},
            }
        )
        row = self.db.latest_quotes()[0]
        self.assertEqual(row["valid_until"], valid_until.isoformat())

    def test_init_schema_migrates_an_older_database(self) -> None:
        """Existing databases gain the column instead of needing a reset."""
        legacy = Path(self.temp_dir.name) / "legacy.sqlite"
        conn = sqlite3.connect(legacy)
        conn.execute(
            """
            CREATE TABLE quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                supplier_id INTEGER NOT NULL,
                observed_at TEXT NOT NULL,
                quantity_liters INTEGER NOT NULL,
                status TEXT NOT NULL,
                price_per_liter REAL,
                total_price REAL,
                currency TEXT NOT NULL,
                source TEXT NOT NULL,
                notes TEXT,
                raw_payload_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.commit()
        conn.close()

        db = Database(legacy)
        db.init_schema()  # must not raise "no such column"

        # closing() matters on Windows: an unclosed sqlite connection keeps the
        # file locked and the TemporaryDirectory cleanup then fails.
        with closing(db.connect()) as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(quotes)")}
        self.assertIn("valid_until", columns)

    def test_migration_is_idempotent(self) -> None:
        self.db.init_schema()
        self.db.init_schema()  # a second run must not fail on the existing column


class ComparisonShowsValidityTests(unittest.TestCase):
    def test_cheapest_reports_how_long_the_offer_stands(self) -> None:
        rows = [
            {
                "supplier_id": 1,
                "supplier_name": "A",
                "website": "https://a.example.com",
                "price_per_liter": 1.0,
                "observed_at": OBSERVED.isoformat(),
                "valid_until": (OBSERVED + timedelta(hours=24)).isoformat(),
            },
            {
                "supplier_id": 2,
                "supplier_name": "B",
                "website": "https://b.example.com",
                "price_per_liter": 1.1,
                "observed_at": OBSERVED.isoformat(),
                "valid_until": (OBSERVED + timedelta(hours=6)).isoformat(),
            },
        ]
        snapshot = AnalyticsService.latest_market_snapshot(rows)
        self.assertEqual(
            snapshot["cheapest_supplier"]["valid_until"],
            (OBSERVED + timedelta(hours=24)).isoformat(),
        )

    def test_current_prices_rows_expose_valid_until(self) -> None:
        """The per-supplier comparison carries it because the column is selected."""
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db = Database(Path(temp_dir.name) / "test.sqlite")
        db.init_schema()
        supplier_id = db.upsert_supplier(
            {
                "name": "A",
                "website": "https://a.example.com",
                "status": "active",
                "connector_type": "manual",
                "connector_config": {},
            }
        )
        db.record_quote(
            {
                "supplier_id": supplier_id,
                "observed_at": OBSERVED.isoformat(),
                "quantity_liters": 1000,
                "status": "ok",
                "price_per_liter": 1.0,
                "total_price": 1000.0,
                "currency": "GBP",
                "source": "test",
                "notes": "",
                "valid_until": (OBSERVED + timedelta(hours=24)).isoformat(),
                "raw_payload": {},
            }
        )
        self.assertIn("valid_until", db.latest_quotes()[0])


if __name__ == "__main__":
    unittest.main()
