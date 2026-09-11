"""Recording the purchase the owner actually made.

The owner buys by phone or on a supplier's own site, then tells us who they
bought from. Nothing here drives a browser: it writes down what happened, so
that "who did I buy from last time, and what did I pay" has an answer.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from oilwatch.service import OilWatchApp


class PurchaseRecordingTests(unittest.TestCase):
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
        for name, website in (
            ("Scottish Fuels", "https://quote.scottishfuels.co.uk/quote/"),
            ("HomeFuels Direct", "https://homefuelsdirect.co.uk/home/heating-oil-prices/aberdeenshire"),
        ):
            self._add_supplier(name, website)
        self.ids = {
            row["name"]: row["id"] for row in self.app.db.list_suppliers(include_inactive=True)
        }

    def _add_supplier(self, name: str, website: str) -> None:
        self.app.db.upsert_supplier(
            {
                "name": name,
                "website": website,
                "status": "active",
                "connector_type": "manual",
                "connector_config": {},
            }
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_records_by_name_and_reads_it_back(self) -> None:
        recorded = self.app.record_purchase(
            "Scottish Fuels", price_per_liter=1.0894, code="autumn25", reference="SF-123"
        )
        self.assertEqual(recorded["supplier_name"], "Scottish Fuels")
        self.assertEqual(recorded["quantity_liters"], 1000)
        self.assertEqual(recorded["agreed_price_per_liter"], 1.0894)
        self.assertEqual(recorded["total_price"], 1089.40)
        self.assertEqual(recorded["discount_code"], "autumn25")
        self.assertEqual(recorded["status"], "ordered")

        purchases = self.app.purchases()
        self.assertEqual(len(purchases), 1)
        self.assertEqual(purchases[0]["supplier_name"], "Scottish Fuels")
        self.assertEqual(purchases[0]["website"], "https://quote.scottishfuels.co.uk/quote/")
        self.assertEqual(purchases[0]["reference"], "SF-123")
        # The code and the total live in the order's payload, not in its columns:
        # reading them back is what makes the history legible.
        self.assertEqual(purchases[0]["discount_code"], "autumn25")
        self.assertEqual(purchases[0]["total_price"], 1089.40)

    def test_a_name_fragment_is_enough(self) -> None:
        """The owner says "scottish", not "Scottish Fuels Ltd"."""
        self.assertEqual(
            self.app.record_purchase("scottish", price_per_liter=1.0894)["supplier_name"],
            "Scottish Fuels",
        )

    def test_the_website_can_identify_the_supplier(self) -> None:
        recorded = self.app.record_purchase("homefuelsdirect", price_per_liter=1.0415)
        self.assertEqual(recorded["supplier_name"], "HomeFuels Direct")

    def test_the_supplier_id_can_identify_the_supplier(self) -> None:
        recorded = self.app.record_purchase(self.ids["Scottish Fuels"], price_per_liter=1.0894)
        self.assertEqual(recorded["supplier_id"], self.ids["Scottish Fuels"])
        self.assertEqual(recorded["supplier_name"], "Scottish Fuels")

    def test_the_total_paid_is_enough(self) -> None:
        recorded = self.app.record_purchase("Scottish Fuels", total_price=1089.40)
        self.assertEqual(recorded["agreed_price_per_liter"], 1.0894)

    def test_the_quantity_overrides_the_usual_order(self) -> None:
        recorded = self.app.record_purchase("Scottish Fuels", quantity_liters=500, price_per_liter=1.20)
        self.assertEqual(recorded["quantity_liters"], 500)
        self.assertEqual(recorded["total_price"], 600.0)

    def test_the_code_is_optional(self) -> None:
        self.assertIsNone(self.app.record_purchase("Scottish Fuels", price_per_liter=1.1144)["discount_code"])

    def test_a_purchase_without_a_price_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            self.app.record_purchase("Scottish Fuels")
        self.assertIn("total paid", str(caught.exception))

    def test_an_unknown_supplier_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            self.app.record_purchase("Nonexistent Oil Co", price_per_liter=1.0)
        self.assertIn("No supplier matches", str(caught.exception))

    def test_an_unknown_id_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            self.app.record_purchase(999, price_per_liter=1.0)
        self.assertIn("Unknown supplier id", str(caught.exception))

    def test_a_blank_supplier_name_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            self.app.record_purchase("   ", price_per_liter=1.0)
        self.assertIn("Name the supplier", str(caught.exception))

    def test_an_ambiguous_supplier_is_refused_rather_than_guessed(self) -> None:
        """A fragment is only enough while one supplier answers to it.

        Filing a purchase against the wrong supplier is worse than asking, and
        the owner's own list carries a depot of the same name, so the fragment
        that resolves above has to be refused here.
        """
        self._add_supplier("Scottish Fuels Depot", "https://quote.scottishfuels.co.uk/depot")

        with self.assertRaises(ValueError) as caught:
            self.app.record_purchase("scottish", price_per_liter=1.0)

        self.assertIn("matches several suppliers", str(caught.exception))
        self.assertEqual(self.app.purchases(), [], "nothing should have been written")

    def test_purchases_are_newest_first(self) -> None:
        self.app.record_purchase(
            "Scottish Fuels", price_per_liter=1.10, ordered_at="2026-09-01T10:00:00"
        )
        self.app.record_purchase(
            "HomeFuels Direct", price_per_liter=1.04, ordered_at="2026-09-10T10:00:00"
        )
        self.assertEqual(
            [row["supplier_name"] for row in self.app.purchases()],
            ["HomeFuels Direct", "Scottish Fuels"],
        )

    def test_a_back_dated_purchase_returns_the_row_it_wrote(self) -> None:
        """Not "the newest row" — a back-dated entry is not the newest."""
        self.app.record_purchase(
            "HomeFuels Direct", price_per_liter=1.04, ordered_at="2026-09-10T10:00:00"
        )
        recorded = self.app.record_purchase(
            "Scottish Fuels", price_per_liter=1.10, ordered_at="2026-09-01T10:00:00"
        )
        self.assertEqual(recorded["supplier_name"], "Scottish Fuels")
        self.assertEqual(recorded["created_at"], "2026-09-01T10:00:00")

    def test_the_limit_is_respected(self) -> None:
        for index in range(3):
            self.app.record_purchase(
                "Scottish Fuels",
                price_per_liter=1.0 + index / 100,
                ordered_at=f"2026-09-0{index + 1}T10:00:00",
            )
        self.assertEqual(len(self.app.purchases(limit=2)), 2)

    def test_status_carries_the_last_purchase(self) -> None:
        self.assertIsNone(self.app.status()["last_purchase"])

        self.app.record_purchase("Scottish Fuels", price_per_liter=1.0894, code="autumn25")
        last = self.app.status()["last_purchase"]
        assert last is not None
        self.assertEqual(last["supplier_name"], "Scottish Fuels")
        self.assertEqual(last["discount_code"], "autumn25")

    def test_a_machine_placed_order_shows_what_it_cost(self) -> None:
        """place_order stores no total in the payload; the columns still know it."""
        self.app.place_order(
            self.ids["Scottish Fuels"], agreed_price_per_liter=1.05, quantity_liters=900
        )

        purchase = self.app.purchases()[0]

        self.assertEqual(purchase["total_price"], 945.0)
        self.assertIsNone(purchase["discount_code"])


if __name__ == "__main__":
    unittest.main()
