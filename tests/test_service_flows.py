"""OilWatchApp over a throwaway install root.

The app is built against a temporary config + database, so the service methods
run for real without touching the repo's data.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from oilwatch.models import QuoteResult, SupplierCandidate, utcnow_naive
from oilwatch.service import OilWatchApp

SETTINGS = {
    "database_path": "data/oilwatch.sqlite",
    "chart_path": "data/chart.png",
    "time_series_chart_path": "data/ts.png",
    "home": {"label": "Hatton of Fintry, Aberdeenshire", "latitude": 57.2, "longitude": -2.2},
    "quote_quantity_liters": 1000,
    "currency": "GBP",
    "max_quote_age_days": 30,
}

OVERRIDES = [
    {"name": "ValueOils", "website": "https://www.valueoils.com", "connector_type": "manual"},
    {"name": "Scottish Fuels", "website": "https://scottishfuels.co.uk", "connector_type": "manual"},
    {"name": "Scottish Fuels Depot", "website": "https://scottishfuels.co.uk/depot", "connector_type": "manual"},
]


class AppTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "data").mkdir()
        (self.root / "config" / "settings.json").write_text(json.dumps(SETTINGS), encoding="utf-8")
        (self.root / "config" / "supplier_overrides.json").write_text(json.dumps(OVERRIDES), encoding="utf-8")
        self.app = OilWatchApp(self.root)

    def _init(self):
        self.app.init()
        return {s["name"]: s["id"] for s in self.app.suppliers()}


class SetupTests(AppTestCase):
    def test_init_creates_the_database_and_imports_the_overrides(self) -> None:
        result = self.app.init()
        self.assertEqual(result["imported_overrides"], len(OVERRIDES))
        self.assertEqual(len(self.app.suppliers()), len(OVERRIDES))

    def test_suppliers_is_empty_until_the_overrides_are_imported(self) -> None:
        self.assertEqual(self.app.suppliers(), [])

    def test_suppliers_can_include_inactive(self) -> None:
        self.app.init()
        self.app.db.mark_missing_suppliers_inactive(["https://www.valueoils.com"])
        self.assertEqual(len(self.app.suppliers(include_inactive=True)), len(OVERRIDES))
        self.assertLess(len(self.app.suppliers()), len(OVERRIDES))


class DiscoveryTests(AppTestCase):
    def test_discover_stores_candidates(self) -> None:
        self.app.init()
        candidate = SupplierCandidate(name="New Fuels", website="https://new.example", query="q", status="active")
        with patch.object(self.app.discovery, "discover", return_value=[candidate]):
            result = self.app.discover_suppliers()

        self.assertEqual(result["stored_suppliers"], 1)
        self.assertIn("New Fuels", [s["name"] for s in self.app.suppliers(include_inactive=True)])


class QuoteTests(AppTestCase):
    def test_quoting_an_unknown_supplier_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.app.quote_supplier(999)

    def test_quote_all_records_an_error_per_failed_supplier_and_notifies(self) -> None:
        self._init()
        with (
            patch.object(self.app.quotes, "quote_supplier", side_effect=RuntimeError("site down")),
            patch("oilwatch.notify.notify_errors") as notify,
        ):
            results = self.app.quote_all()

        self.assertEqual(len(results), len(OVERRIDES))
        self.assertTrue(all(r["status"] == "error" for r in results))
        self.assertIn("site down", results[0]["notes"])
        notify.assert_called_once()

    def test_quote_all_does_not_notify_when_nothing_failed(self) -> None:
        self._init()
        manual = QuoteResult(
            supplier_id=1,
            supplier_name="Supplier",
            observed_at=utcnow_naive(),
            quantity_liters=1000,
            status="manual_action_required",
            source="manual",
        )
        with (
            patch.object(self.app.quotes, "quote_supplier", return_value=manual),
            patch("oilwatch.notify.notify_errors") as notify,
        ):
            results = self.app.quote_all()

        self.assertTrue(all(r["status"] == "manual_action_required" for r in results))
        notify.assert_not_called()


class ReportingTests(AppTestCase):
    def test_cheapest_and_current_prices_are_answerable_with_an_empty_database(self) -> None:
        snapshot = self.app.cheapest()
        self.assertIsInstance(snapshot, dict)
        self.assertEqual(self.app.current_prices(), [])

    def test_status_includes_the_snapshot_trend_and_last_purchase(self) -> None:
        self._init()
        self.app.record_purchase("ValueOils", price_per_liter=1.05)

        status = self.app.status()

        self.assertIn("market_snapshot", status)
        self.assertIn("trend", status)
        self.assertIn("recommendation", status)
        self.assertEqual(status["last_purchase"]["supplier_name"], "ValueOils")


class EmailMonitorTests(AppTestCase):
    def test_monitor_email_returns_what_was_recorded(self) -> None:
        monitor = MagicMock()
        monitor.run.return_value = [{"supplier_id": 1}]
        with patch("oilwatch.graph_email.GraphEmailMonitor", return_value=monitor):
            self.assertEqual(self.app.monitor_email(), {"recorded": [{"supplier_id": 1}]})

    def test_monitor_email_turns_a_failure_into_a_message(self) -> None:
        monitor = MagicMock()
        monitor.run.side_effect = RuntimeError("not authenticated")
        with patch("oilwatch.graph_email.GraphEmailMonitor", return_value=monitor):
            self.assertEqual(self.app.monitor_email(), {"recorded": [], "error": "not authenticated"})


class MaintenanceTests(AppTestCase):
    def test_import_spreadsheet_requires_a_path(self) -> None:
        with self.assertRaises(ValueError):
            self.app.import_spreadsheet()


class PlaceOrderTests(AppTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ids = self._init()

    def test_an_unknown_supplier_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.app.place_order(999, agreed_price_per_liter=1.05)

    def test_an_order_is_recorded(self) -> None:
        result = self.app.place_order(self.ids["ValueOils"], agreed_price_per_liter=1.05, quantity_liters=900)
        self.assertEqual(result["status"], "manual_action_required")
        self.assertEqual(result["quantity_liters"], 900)


if __name__ == "__main__":
    unittest.main()
