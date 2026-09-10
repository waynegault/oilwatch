from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from oilwatch.discounts import effective_price_per_litre, parse_discounts
from oilwatch.service import OilWatchApp

RECEIVED = datetime(2026, 9, 10, 9, 0, 0)
VALUEOILS_OFFERS = parse_discounts(
    "£12 OFF 1,000-1,999 litres - Code: KJHA154306\nexpires in 48 hours", received_at=RECEIVED
)


class EffectivePriceUnitTests(unittest.TestCase):
    def test_without_a_discount_the_price_is_unchanged(self) -> None:
        self.assertEqual(effective_price_per_litre(1.0415, 1000, None), 1.0415)

    def test_discount_is_spread_across_the_order(self) -> None:
        """£12 over 1,000L is 1.2p/L off — comparable to a week of price drift."""
        self.assertEqual(effective_price_per_litre(1.1144, 1000, VALUEOILS_OFFERS[0]), 1.1024)

    def test_a_larger_order_dilutes_the_same_discount(self) -> None:
        offer = VALUEOILS_OFFERS[0]
        small = effective_price_per_litre(1.10, 1000, offer)
        large = effective_price_per_litre(1.10, 2000, offer)
        self.assertLess(small, 1.10)
        self.assertGreater(large, small, "£12 off 2,000L is a smaller per-litre saving")

    def test_zero_quantity_does_not_divide_by_zero(self) -> None:
        self.assertEqual(effective_price_per_litre(1.10, 0, VALUEOILS_OFFERS[0]), 1.10)


class ComparisonShowsEffectivePriceTests(unittest.TestCase):
    """The comparison must state what the order actually costs, VAT and all."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "config").mkdir(parents=True, exist_ok=True)
        (self.root / "data").mkdir(parents=True, exist_ok=True)
        settings = {
            "database_path": "data/test.sqlite",
            "chart_path": "data/test-chart.png",
            "home": {"label": "Hatton of Fintray", "latitude": 57.251, "longitude": -2.242},
            "radius_miles": 50,
            "quote_quantity_liters": 1000,
            "currency": "GBP",
            "search_queries": [],
            "excluded_domains": [],
            "scheduler": {"discovery_interval_hours": 168, "quote_interval_hours": 24},
        }
        (self.root / "config" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        self.app = OilWatchApp(self.root)
        self.app.init()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _supplier(self, name: str) -> int:
        return self.app.db.upsert_supplier(
            {
                "name": name,
                "website": f"https://{name.lower()}.example.com",
                "status": "active",
                "connector_type": "manual",
                "connector_config": {},
            }
        )

    def _quote(self, supplier_id: int, price: float) -> None:
        self.app.db.record_quote(
            {
                "supplier_id": supplier_id,
                "observed_at": "2026-09-10T12:00:00",
                "quantity_liters": 1000,
                "status": "ok",
                "price_per_liter": price,
                "total_price": price * 1000,
                "currency": "GBP",
                "source": "test",
                "notes": "",
                "valid_until": "2026-09-11T12:00:00",
                "raw_payload": {},
            }
        )

    def test_effective_price_reflects_the_best_code_for_the_quantity(self) -> None:
        supplier_id = self._supplier("ValueOils")
        self._quote(supplier_id, 1.1144)
        # Two codes: the smaller one does not cover 1,000L, so the £12 one wins.
        self.app.db.record_discount(
            {
                "supplier_id": supplier_id,
                "code": "SMALL10",
                "amount_gbp": 10.0,
                "min_litres": 500,
                "max_litres": 999,
                "expires_at": (RECEIVED + timedelta(hours=48)).isoformat(),
                "source": "email",
                "observed_at": RECEIVED.isoformat(),
            }
        )
        self.app.db.record_discount(
            {
                "supplier_id": supplier_id,
                "code": "KJHA154306",
                "amount_gbp": 12.0,
                "min_litres": 1000,
                "max_litres": 1999,
                "expires_at": (RECEIVED + timedelta(hours=48)).isoformat(),
                "source": "email",
                "observed_at": RECEIVED.isoformat(),
            }
        )

        row = self.app.current_prices()[0]
        self.assertEqual(row["price_per_liter"], 1.1144, "headline price is still shown")
        self.assertEqual(row["effective_price_per_liter"], 1.1024)
        self.assertEqual(row["effective_total_price"], 1102.40)
        self.assertEqual(row["discount"]["code"], "KJHA154306")

    def test_the_snapshot_winner_carries_the_effective_price(self) -> None:
        supplier_id = self._supplier("ValueOils")
        self._quote(supplier_id, 1.1144)
        self.app.db.record_discount(
            {
                "supplier_id": supplier_id,
                "code": "KJHA154306",
                "amount_gbp": 12.0,
                "min_litres": 1000,
                "max_litres": 1999,
                "expires_at": None,
                "source": "email",
                "observed_at": RECEIVED.isoformat(),
            }
        )

        cheapest = self.app.cheapest()["cheapest_supplier"]
        self.assertEqual(cheapest["price_per_liter"], 1.1144)
        self.assertEqual(cheapest["effective_price_per_liter"], 1.1024)
        self.assertEqual(cheapest["discount"]["code"], "KJHA154306")

    def test_without_any_code_the_effective_price_equals_the_headline(self) -> None:
        self._quote(self._supplier("Highland Fuels"), 1.1287)

        row = self.app.current_prices()[0]
        self.assertEqual(row["effective_price_per_liter"], row["price_per_liter"])
        self.assertIsNone(row["discount"])

    def test_an_expired_code_is_not_applied(self) -> None:
        supplier_id = self._supplier("ValueOils")
        self._quote(supplier_id, 1.1144)
        self.app.db.record_discount(
            {
                "supplier_id": supplier_id,
                "code": "STALE",
                "amount_gbp": 50.0,
                "min_litres": 1000,
                "max_litres": None,
                "expires_at": "2020-01-01T00:00:00",
                "source": "email",
                "observed_at": "2020-01-01T00:00:00",
            }
        )

        row = self.app.current_prices()[0]
        self.assertIsNone(row["discount"])
        self.assertEqual(row["effective_price_per_liter"], 1.1144)


    def test_the_ranking_uses_the_effective_price(self) -> None:
        """A code can make the higher headline price the cheaper way to buy."""
        headline_cheap = self._supplier("CheapHeadline")
        coded = self._supplier("PriceyWithCode")
        self._quote(headline_cheap, 1.0400)
        self._quote(coded, 1.0500)  # £15 off 1,000L brings this to 1.0350
        self.app.db.record_discount(
            {
                "supplier_id": coded,
                "code": "BIG15",
                "amount_gbp": 15.0,
                "min_litres": 1000,
                "max_litres": None,
                "expires_at": None,
                "source": "email",
                "observed_at": RECEIVED.isoformat(),
            }
        )

        winner = self.app.cheapest()["cheapest_supplier"]
        self.assertEqual(winner["name"], "PriceyWithCode")
        self.assertEqual(winner["price_per_liter"], 1.0500, "the headline is still reported")
        self.assertEqual(winner["effective_price_per_liter"], 1.0350)
        self.assertEqual(winner["discount"]["code"], "BIG15")

    def test_an_expired_code_does_not_reorder_the_market(self) -> None:
        headline_cheap = self._supplier("CheapHeadline")
        stale = self._supplier("ExpiredCode")
        self._quote(headline_cheap, 1.0400)
        self._quote(stale, 1.0500)
        self.app.db.record_discount(
            {
                "supplier_id": stale,
                "code": "STALE",
                "amount_gbp": 15.0,
                "min_litres": 1000,
                "max_litres": None,
                "expires_at": "2020-01-01T00:00:00",
                "source": "email",
                "observed_at": "2020-01-01T00:00:00",
            }
        )

        self.assertEqual(self.app.cheapest()["cheapest_supplier"]["name"], "CheapHeadline")


class ProcessedMessageLedgerTests(unittest.TestCase):
    """Sweeping old mail needs a ledger, or the same quote is recorded twice."""

    def _db(self):
        from oilwatch.db import Database

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db = Database(Path(temp_dir.name) / "test.sqlite")
        db.init_schema()
        return db

    def test_a_message_is_only_reported_processed_once_marked(self) -> None:
        db = self._db()
        self.assertFalse(db.message_processed("msg-1"))
        db.mark_message_processed("msg-1")
        self.assertTrue(db.message_processed("msg-1"))

    def test_marking_twice_is_harmless(self) -> None:
        db = self._db()
        db.mark_message_processed("msg-1")
        db.mark_message_processed("msg-1")  # must not raise on the primary key
        self.assertTrue(db.message_processed("msg-1"))

    def test_a_blank_id_is_never_processed(self) -> None:
        db = self._db()
        self.assertFalse(db.message_processed(""))
        db.mark_message_processed("")  # must not write a row for a blank id
        self.assertFalse(db.message_processed(""))


if __name__ == "__main__":
    unittest.main()
