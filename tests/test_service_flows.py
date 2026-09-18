"""OilWatchApp over a throwaway install root.

The app is built against a temporary config + database, so the service methods
run for real without touching the repo's data. The root itself lives in
``tests/app_fixture.py``, shared with the purchase-recording suite.
"""

from __future__ import annotations

import threading
import time
import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

from oilwatch.models import QuoteResult, SupplierCandidate, utcnow_naive
from tests.app_fixture import OVERRIDES, AppTestCase


def _recording_quote(threads: set[str]):
    """A ``quote_supplier`` stand-in that records which threads ran it."""

    def quote(supplier, quantity, postcode=None, prefer_browser=False):
        threads.add(threading.current_thread().name)
        # Hold the worker briefly so a second task can start on another thread
        # while this one is still busy.
        time.sleep(0.05)
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=utcnow_naive(),
            quantity_liters=quantity,
            status="manual_action_required",
            source="manual",
        )

    return quote


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

    def test_quote_all_quotes_suppliers_concurrently(self) -> None:
        """The per-supplier browser launches are fanned out, not serialised.

        Each quote is a browser launch of 10-30s, so a sequential run scaled
        linearly with the supplier count. The cap is ``quote_max_workers``; this
        pins that more than one worker is actually used.
        """
        self._init()
        threads: set[str] = set()
        with patch.object(self.app.quotes, "quote_supplier", side_effect=_recording_quote(threads)):
            self.app.quote_all()

        self.assertGreater(len(threads), 1, "quote_all should quote on more than one worker")

    def test_quote_all_can_be_pinned_to_one_worker(self) -> None:
        """``max_workers=1`` is the escape hatch back to strictly sequential."""
        self._init()
        threads: set[str] = set()
        with patch.object(self.app.quotes, "quote_supplier", side_effect=_recording_quote(threads)):
            self.app.quote_all(max_workers=1)

        self.assertEqual(len(threads), 1)

    def test_quote_all_keeps_the_supplier_order_of_its_results(self) -> None:
        """Concurrency must not reorder results — ``map`` is ordered."""
        self._init()
        with patch.object(self.app.quotes, "quote_supplier", side_effect=_recording_quote(set())):
            results = self.app.quote_all()

        self.assertEqual(
            [r["supplier_name"] for r in results],
            [s["name"] for s in self.app.suppliers()],
        )

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

    def test_both_market_tools_report_the_configured_window(self) -> None:
        """The window travels from settings into the output, not just the query.

        Pinned at this layer because the analytics test passes the value in
        directly: a caller that stopped passing it would leave the empty-market
        case ambiguous again with that test still green.
        """
        window = self.app.settings.max_quote_age_days
        self.assertEqual(self.app.cheapest()["window_days"], window)
        self.assertEqual(self.app.status()["market_snapshot"]["window_days"], window)

    def test_cheapest_flags_a_supplier_whose_latest_attempt_failed(self) -> None:
        """A price from an earlier run is named, not passed off as this run's.

        Scottish Fuels' shape: a good quote, then an attempt that returned no
        price. The good quote still counts (the window is a day), so it is
        flagged alongside the market rather than silently shown as current.
        """
        ids = self._init()
        now = utcnow_naive()
        for observed, status, price in (
            ((now - timedelta(hours=2)).isoformat(), "ok", 1.10),
            ((now - timedelta(hours=1)).isoformat(), "manual_action_required", None),
        ):
            self.app.db.record_quote(
                {
                    "supplier_id": ids["Scottish Fuels"],
                    "observed_at": observed,
                    "quantity_liters": 1000,
                    "status": status,
                    "price_per_liter": price,
                    "total_price": None if price is None else price * 1000,
                    "currency": "GBP",
                    "source": "scottish_fuels_browser",
                    "notes": "session expired and the automatic sign-in did not take",
                    "raw_payload": {},
                }
            )

        flagged = self.app.cheapest()["not_refreshed_suppliers"]

        self.assertEqual([row["name"] for row in flagged], ["Scottish Fuels"])
        self.assertEqual(flagged[0]["price_per_liter"], 1.10)
        self.assertEqual(flagged[0]["last_attempt_status"], "manual_action_required")
        self.assertIn("session expired", flagged[0]["last_attempt_note"])

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


#: Suppliers for the ordering-page tests: one whose config names the page a human
#: orders from, one that names none. ``AppTestCase`` writes these to the
#: throwaway root's overrides file, and ``init()`` imports them.
ORDERING_OVERRIDES = [
    {
        "name": "Formy Fuels",
        "website": "https://formy.example.com/",
        "connector_type": "manual",
        "connector_config": {"order_page": "https://formy.example.com/order"},
    },
    {
        "name": "Marketing Only",
        "website": "https://marketing.example.com/",
        "connector_type": "manual",
    },
]


class OrderingPageTests(AppTestCase):
    """The page a human orders from, carried as its own field.

    ``website`` is frequently a marketing page, so a supplier whose own config
    names the ordering page carries it as ``order_page`` — and one that names
    none reports None rather than falling back to the URL the connector scrapes,
    which for some suppliers is an API endpoint rather than anything clickable.
    """

    overrides = ORDERING_OVERRIDES

    def _record(self, supplier_id: int, payload: dict) -> None:
        self.app.db.record_quote(
            {
                "supplier_id": supplier_id,
                "observed_at": utcnow_naive().isoformat(),
                "quantity_liters": 1000,
                "status": "ok",
                "price_per_liter": 1.10,
                "total_price": 1100.0,
                "currency": "GBP",
                "source": "test",
                "notes": "",
                "raw_payload": payload,
            }
        )

    def test_the_ordering_page_comes_from_the_supplier_config(self) -> None:
        ids = self._init()
        self._record(
            ids["Formy Fuels"], {"quote_url": "https://formy.example.com/api/getoffers.php"}
        )
        self._record(ids["Marketing Only"], {"quote_url": "https://marketing.example.com/api/quote"})

        rows = {row["supplier_name"]: row for row in self.app.current_prices()}

        self.assertEqual(rows["Formy Fuels"]["order_page"], "https://formy.example.com/order")
        # The scrape endpoint in the quote's payload is deliberately not used:
        # for Highland Fuels that value is an API endpoint, so promoting it would
        # hand an agent a link that cannot be ordered from.
        self.assertIsNone(rows["Marketing Only"]["order_page"])

    def test_the_winner_carries_its_ordering_page(self) -> None:
        ids = self._init()
        self._record(ids["Formy Fuels"], {})

        cheapest = self.app.cheapest()["cheapest_supplier"]

        self.assertEqual(cheapest["name"], "Formy Fuels")
        self.assertEqual(cheapest["order_page"], "https://formy.example.com/order")


if __name__ == "__main__":
    unittest.main()
