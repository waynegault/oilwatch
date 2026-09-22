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
from oilwatch.service import SWEEP_STALE_AFTER_MINUTES
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
        # Classified rather than left to the message: "site_error" is the one
        # reason every raising path shares, and it is what a consumer branches on
        # instead of parsing prose.
        self.assertTrue(all(r["reason"] == "site_error" for r in results))
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
        # The price read is an envelope now, so "answerable" means its `quotes`
        # list is empty rather than that the call returns a bare [].
        self.assertEqual(self.app.current_prices()["quotes"], [])

    def test_the_price_envelope_says_why_an_empty_market_is_empty(self) -> None:
        """The first call an agent makes must not be a dead end.

        With suppliers on record and nothing quoted yet, an empty ``quotes`` says
        nothing on its own. ``never_quoted`` is what separates "nobody has ever
        been asked" from "nothing is fresh enough" — the difference between
        needing a refresh and needing a connector.
        """
        names = set(self._init())

        prices = self.app.current_prices()

        self.assertEqual(prices["quotes"], [])
        self.assertIsNone(prices["as_of"])
        self.assertEqual(prices["window_days"], self.app.settings.max_quote_age_days)
        self.assertEqual(prices["excluded_suppliers"], [])
        self.assertEqual(prices["not_refreshed_suppliers"], [])
        self.assertEqual({row["supplier_name"] for row in prices["never_quoted"]}, names)

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
        for observed, status, price, reason in (
            ((now - timedelta(hours=2)).isoformat(), "ok", 1.10, None),
            (
                (now - timedelta(hours=1)).isoformat(),
                "manual_action_required",
                None,
                "login_not_confirmed",
            ),
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
                    "reason": reason,
                    "raw_payload": {},
                }
            )

        flagged = self.app.cheapest()["not_refreshed_suppliers"]

        self.assertEqual([row["name"] for row in flagged], ["Scottish Fuels"])
        self.assertEqual(flagged[0]["price_per_liter"], 1.10)
        self.assertEqual(flagged[0]["last_attempt_status"], "manual_action_required")
        self.assertIn("session expired", flagged[0]["last_attempt_note"])
        # The machine-readable companion to that note, and the reason this
        # supplier needs a sign-in rather than a connector fix. It survives the
        # round trip through the database column, which is the point: a consumer
        # reading a later run gets the reason, not just the prose.
        self.assertEqual(flagged[0]["last_attempt_reason"], "login_not_confirmed")

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

        rows = {row["supplier_name"]: row for row in self.app.current_prices()["quotes"]}

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


#: One supplier per way of ordering, so every branch of the derivation has a case
#: that fails if it changes. The benchmark deliberately *also* has a page and a
#: phone number: ``benchmark`` has to win over both, because a figure you cannot
#: buy from must never come back looking orderable. A phone number on its own is
#: deliberately *not* a branch: the app never rings a supplier, so that row falls
#: through to ``none``.
ORDER_CHANNEL_OVERRIDES = [
    {
        "name": "Page Only",
        "website": "https://page.example.com/",
        "connector_type": "manual",
        "connector_config": {"order_page": "https://page.example.com/order"},
    },
    {
        "name": "Both Contacts",
        "website": "https://both.example.com/",
        "connector_type": "manual",
        "phone": "01234 567890",
        "email": "sales@both.example.com",
    },
    {
        "name": "Phone Only",
        "website": "https://phone.example.com/",
        "connector_type": "manual",
        "phone": "01234 567890",
    },
    {
        "name": "Email Only",
        "website": "https://email.example.com/",
        "connector_type": "manual",
        "email": "sales@email.example.com",
    },
    {
        "name": "Nothing Recorded",
        "website": "https://nothing.example.com/",
        "connector_type": "manual",
    },
    {
        "name": "A Benchmark",
        "website": "https://bench.example.com/",
        "kind": "benchmark",
        "connector_type": "manual",
        "phone": "01234 567890",
        "connector_config": {"order_page": "https://bench.example.com/order"},
    },
]


class OrderChannelTests(AppTestCase):
    """What a row *is*, and how to act on it, as fields rather than prose.

    A consumer of these tools holds rules it must not get wrong — Fueltool is a
    benchmark and must never be presented as the winner; every price goes out
    with a link to order from. Both were enforceable only by reading a note and
    remembering it. These tests pin the fields that replace that reading.
    """

    overrides = ORDER_CHANNEL_OVERRIDES

    def _record(self, supplier_id: int, price: float = 1.10) -> None:
        self.app.db.record_quote(
            {
                "supplier_id": supplier_id,
                "observed_at": utcnow_naive().isoformat(),
                "quantity_liters": 1000,
                "status": "ok",
                "price_per_liter": price,
                "total_price": price * 1000,
                "currency": "GBP",
                "source": "test",
                "notes": "",
                "raw_payload": {},
            }
        )

    def _rows(self) -> dict[str, dict]:
        for supplier_id in self._init().values():
            self._record(supplier_id)
        return {row["supplier_name"]: row for row in self.app.current_prices()["quotes"]}

    def test_each_supplier_reports_how_it_is_ordered_from(self) -> None:
        rows = self._rows()
        self.assertEqual(
            {
                name: rows[name]["order_channel"]
                for name in (
                    "Page Only",
                    "Both Contacts",
                    "Phone Only",
                    "Email Only",
                    "Nothing Recorded",
                    "A Benchmark",
                )
            },
            {
                "Page Only": "web",
                "Both Contacts": "email",
                "Phone Only": "none",
                "Email Only": "email",
                "Nothing Recorded": "none",
                "A Benchmark": "benchmark",
            },
        )

    def test_a_phone_number_alone_never_reads_as_a_route(self) -> None:
        """The removal this pins: there is no phone ask, so no phone channel.

        A row whose only recorded contact is a number reports ``none`` rather
        than ``phone``: the app asks by form or by email, and naming a channel
        an agent could act on would invent a call nobody makes. The number still
        travels as contact data.
        """
        row = self._rows()["Phone Only"]

        self.assertEqual(row["order_channel"], "none")
        self.assertEqual(row["contact"]["phone"], "01234 567890")

    def test_a_benchmark_is_never_orderable_even_when_it_has_a_page(self) -> None:
        """The precedence that matters: a figure you cannot buy from.

        Fueltool is scraped from a public web page and has a phone-shaped record
        in no sense — but even if a benchmark row carried both, reporting it as
        ``web`` is the mistake this field exists to prevent.
        """
        row = self._rows()["A Benchmark"]
        self.assertEqual(row["kind"], "benchmark")
        self.assertEqual(row["order_channel"], "benchmark")

    def test_the_contact_carries_the_one_url_to_act_on(self) -> None:
        rows = self._rows()
        self.assertEqual(rows["Page Only"]["contact"]["url"], "https://page.example.com/order")
        # No ordering page recorded, so the site is what a reader is given —
        # even though it may only be a marketing page.
        self.assertEqual(rows["Nothing Recorded"]["contact"]["url"], "https://nothing.example.com/")
        self.assertEqual(rows["Both Contacts"]["contact"]["phone"], "01234 567890")
        self.assertEqual(rows["Both Contacts"]["contact"]["email"], "sales@both.example.com")

    def test_a_never_quoted_supplier_still_says_how_to_ask_it(self) -> None:
        """The rows a reader has to *ask* need these fields most of all.

        A supplier with no quote row at all comes back in ``never_quoted``, and a
        bare "no price yet" that names no page and no number is the least
        actionable row in the envelope.
        """
        self._init()
        by_name = {row["supplier_name"]: row for row in self.app.current_prices()["never_quoted"]}

        self.assertEqual(by_name["Page Only"]["order_channel"], "web")
        self.assertEqual(by_name["Page Only"]["contact"]["url"], "https://page.example.com/order")
        # A number alone is not a channel, but it is still contact data.
        self.assertEqual(by_name["Phone Only"]["order_channel"], "none")
        self.assertEqual(by_name["Phone Only"]["contact"]["phone"], "01234 567890")

    def test_an_unpriced_attempt_still_says_how_to_ask_it(self) -> None:
        """The same for the suppliers the last ask could not price.

        These five are the ones that quote only on request, so
        ``quote_by_request`` is exactly where an ordering link has to travel with
        the reason rather than in a separate lookup.
        """
        ids = self._init()
        self.app.db.record_quote(
            {
                "supplier_id": ids["Page Only"],
                "observed_at": utcnow_naive().isoformat(),
                "quantity_liters": 1000,
                "status": "manual_action_required",
                "price_per_liter": None,
                "total_price": None,
                "currency": "GBP",
                "source": "test",
                "reason": "quote_by_request",
                "notes": "",
                "raw_payload": {},
            }
        )

        entry = next(
            row
            for row in self.app.current_prices()["no_quote_suppliers"]
            if row["supplier_name"] == "Page Only"
        )

        self.assertEqual(entry["reason"], "quote_by_request")
        self.assertEqual(entry["order_channel"], "web")
        self.assertEqual(entry["contact"]["url"], "https://page.example.com/order")

    def test_the_winner_carries_its_kind_and_contact(self) -> None:
        for name, supplier_id in self._init().items():
            self._record(supplier_id, price=1.10 if name == "Page Only" else 1.30)

        cheapest = self.app.cheapest()["cheapest_supplier"]

        self.assertEqual(cheapest["name"], "Page Only")
        self.assertEqual(cheapest["kind"], "supplier")
        self.assertEqual(cheapest["order_channel"], "web")
        self.assertEqual(cheapest["contact"]["url"], "https://page.example.com/order")


class RefreshCooldownStateTests(AppTestCase):
    """The age a cooldown is measured against, read from the database.

    The tool's own tests mock this out, so the arithmetic and the choice of
    observation are checked here: it is the newest one *of any kind*, because
    the question is "when did we last go and look?".
    """

    def _quote(self, supplier_id: int, observed) -> None:
        self.app.db.record_quote(
            {
                "supplier_id": supplier_id,
                "observed_at": observed.isoformat(),
                "quantity_liters": 1000,
                "status": "ok",
                "price_per_liter": 1.10,
                "total_price": 1100.0,
                "currency": "GBP",
                "source": "test",
                "notes": "",
                "raw_payload": {},
            }
        )

    def test_the_age_comes_from_the_newest_observation(self) -> None:
        ids = self._init()
        now = utcnow_naive()
        self._quote(ids["ValueOils"], now - timedelta(minutes=90))
        self._quote(ids["Scottish Fuels"], now - timedelta(minutes=3))

        recent = self.app.refresh_recently_done(10)

        self.assertIsNotNone(recent)
        assert recent is not None  # narrowed for the type checker
        self.assertGreaterEqual(recent["minutes_ago"], 2.9)
        self.assertLess(recent["minutes_ago"], 4.0)

    def test_a_sweep_older_than_the_window_is_not_recent(self) -> None:
        ids = self._init()
        self._quote(ids["ValueOils"], utcnow_naive() - timedelta(minutes=30))

        self.assertIsNone(self.app.refresh_recently_done(10))

    def test_nothing_on_record_is_not_recent(self) -> None:
        """'No age to report' must not read as 'zero minutes ago'.

        Confusing the two would block the very first sweep of a new database,
        which is the one refresh that certainly should happen.
        """
        self._init()

        self.assertIsNone(self.app.refresh_recently_done(10))


class SweepMarkerTests(AppTestCase):
    """Still running, or dead? — the question a timed-out caller cannot answer.

    A sweep's quote rows appear only as each supplier finishes, so a sweep thirty
    seconds in has left nothing on record. The marker answers it, and the three
    states have to stay distinct: running (block a second sweep), finished, and
    "started but never reported back" (which is a crash, not progress).
    """

    overrides = None

    def test_no_sweep_at_all_is_not_in_progress(self) -> None:
        self.app.db.init_schema()
        state = self.app.sweep_state()
        self.assertFalse(state["in_progress"])
        self.assertFalse(state["stale"])
        self.assertIsNone(state["started_at"])

    def test_a_fresh_marker_reads_as_in_progress(self) -> None:
        self.app.db.init_schema()
        started = utcnow_naive().isoformat()
        self.app.db.start_sweep(started, "mcp")

        state = self.app.sweep_state()

        self.assertTrue(state["in_progress"])
        self.assertFalse(state["stale"], "a running sweep is not a dead one")
        self.assertEqual(state["started_by"], "mcp")
        self.assertLess(state["seconds_ago"], 5)

    def test_a_finished_marker_stops_reading_as_in_progress(self) -> None:
        self.app.db.init_schema()
        started = utcnow_naive().isoformat()
        self.app.db.start_sweep(started, "cli")
        self.app.db.finish_sweep(started)

        state = self.app.sweep_state()

        self.assertFalse(state["in_progress"])
        self.assertFalse(state["stale"])
        self.assertEqual(state["started_at"], started)
        self.assertIsNotNone(state["finished_at"])

    def test_a_marker_that_never_finished_reads_as_stale_not_running(self) -> None:
        """A crashed sweep must not look like progress for the rest of the day.

        Ten minutes is past any sweep, so a marker older than that is a run that
        died — reporting it as still running would be a lie the next caller acts
        on by waiting.
        """
        self.app.db.init_schema()
        old = utcnow_naive() - timedelta(minutes=SWEEP_STALE_AFTER_MINUTES + 1)
        self.app.db.start_sweep(old.isoformat(), "scheduler")

        state = self.app.sweep_state()

        self.assertFalse(state["in_progress"])
        self.assertTrue(state["stale"])
        self.assertGreater(state["seconds_ago"], SWEEP_STALE_AFTER_MINUTES * 60)

    def test_a_marker_from_the_future_reports_as_just_now(self) -> None:
        """A clock that moves can leave a marker dated ahead of now.

        "started -3700 seconds ago" is worse than useless in a report, and the
        answer to the question being asked — is it running? — is unaffected.
        """
        self.app.db.init_schema()
        self.app.db.start_sweep(
            (utcnow_naive() + timedelta(minutes=5)).isoformat(), "cli"
        )

        state = self.app.sweep_state()

        self.assertEqual(state["seconds_ago"], 0)
        self.assertTrue(state["in_progress"])

    def test_a_sweep_marks_itself_and_clears_the_marker(self) -> None:
        """The marker is written by the sweep itself, not by its callers.

        Every entry point — MCP, CLI, scheduler — goes through ``quote_all``, so
        the marker cannot be sidestepped by a path that forgets to set it.
        """
        self._init()
        # No suppliers are quotable here, which is the point: the marker has to be
        # cleared whether the sweep succeeds, fails per supplier, or raises.
        self.app.quote_all(started_by="cli", max_workers=1)

        state = self.app.sweep_state()

        self.assertFalse(state["in_progress"])
        self.assertEqual(state["started_by"], "cli")
        self.assertIsNotNone(state["finished_at"], "the marker was left standing")


class RefreshJobTests(AppTestCase):
    """A detached sweep, readable after the caller that asked for it is gone.

    The job row is written by the launcher and updated by the worker, so the id
    is readable from the moment it is returned and the outcome survives both
    processes. Three states again, and for the same reason: running, finished,
    and "the worker is gone" — which must not read as progress.
    """

    overrides = None

    def _job(self, job_id: str = "job-1", age_minutes: int = 0, total: int = 3) -> str:
        self.app.db.init_schema()
        started = (utcnow_naive() - timedelta(minutes=age_minutes)).isoformat()
        self.app.db.create_refresh_job(job_id, started, "mcp", total=total)
        return job_id

    def test_a_new_job_reads_as_running_from_the_moment_it_is_returned(self) -> None:
        """The row is created by the launcher, not the worker.

        Otherwise the client that just received a job_id would be told there is
        no such job, which is exactly when it is most likely to ask.
        """
        job_id = self._job()
        status = self.app.refresh_job_status(job_id)

        self.assertTrue(status["found"])
        self.assertEqual(status["state"], "running")
        self.assertFalse(status["stale"])
        self.assertEqual((status["done"], status["total"]), (0, 3))

    def test_progress_and_results_are_readable(self) -> None:
        job_id = self._job()
        self.app.db.progress_refresh_job(job_id, 2)
        self.assertEqual(self.app.refresh_job_status(job_id)["done"], 2)

        self.app.db.finish_refresh_job(job_id, "finished", results=[{"supplier_name": "A"}])
        status = self.app.refresh_job_status(job_id)

        self.assertEqual(status["state"], "finished")
        self.assertFalse(status["stale"])
        self.assertEqual(status["results"], [{"supplier_name": "A"}])
        self.assertIsNotNone(status["finished_at"])

    def test_a_job_whose_worker_vanished_reads_as_stale(self) -> None:
        """The state a killed worker leaves must not look like progress.

        Nothing rewrites it — the process that would have is the one that died —
        so the reader has to work it out from the age.
        """
        status = self.app.refresh_job_status(
            self._job(age_minutes=SWEEP_STALE_AFTER_MINUTES + 1)
        )

        self.assertEqual(status["state"], "running")
        self.assertTrue(status["stale"])

    def test_an_unknown_job_is_not_found_rather_than_an_error(self) -> None:
        self.app.db.init_schema()
        self.assertEqual(
            self.app.refresh_job_status("nope"), {"found": False, "job_id": "nope"}
        )

    def test_no_id_reads_the_newest_job(self) -> None:
        self._job("older")
        self.app.db.create_refresh_job("newer", utcnow_naive().isoformat(), "cli", total=1)

        self.assertEqual(self.app.refresh_job_status()["job_id"], "newer")

    def test_running_a_job_records_it_finished(self) -> None:
        job_id = self._job(total=0)
        self.app.run_refresh_job(job_id)  # this fixture has no suppliers
        status = self.app.refresh_job_status(job_id)

        self.assertEqual(status["state"], "finished")
        self.assertEqual(status["results"], [])

    def test_a_job_that_raises_is_recorded_as_failed_and_still_raises(self) -> None:
        """The worker's exit code matters to whoever runs it by hand.

        Swallowing the exception would leave a debugging session with a zero exit
        and no reason, so it is recorded on the job *and* re-raised.
        """
        job_id = self._job(total=1)
        with (
            patch.object(self.app, "quote_all", side_effect=RuntimeError("browser died")),
            self.assertRaises(RuntimeError),
        ):
            self.app.run_refresh_job(job_id)

        status = self.app.refresh_job_status(job_id)
        self.assertEqual(status["state"], "failed")
        self.assertIn("browser died", status["error"])

    def test_a_background_sweep_spawns_a_detached_worker(self) -> None:
        """The command is what matters: it re-enters the CLI with the job id.

        A separate process rather than a thread, because the session that asks
        for a background sweep is the one expected to end before it finishes.
        """
        self._init()
        with patch("oilwatch.service.subprocess.Popen") as popen:
            started = self.app.start_background_sweep(started_by="mcp")

        command = popen.call_args.args[0]
        self.assertIn("quote-all", command)
        self.assertIn("--job-id", command)
        self.assertIn(started["job_id"], command)
        # From the checkout, so the worker reads the same config and database.
        self.assertEqual(popen.call_args.kwargs["cwd"], str(self.root))
        # And the id it returned is already readable, before the worker starts.
        self.assertEqual(self.app.refresh_job_status(started["job_id"])["state"], "running")


class SupplierOutcomeTests(AppTestCase):
    """Which suppliers gave no price, and which ones we failed to retrieve one from.

    Two lists because they read differently: "no web quote to fetch" is the
    supplier doing what it does, where a failed retrieval is a fault. ``reason``
    carries the difference, so neither list needs its notes read.
    """

    def _attempt(self, supplier_id: int, status: str, reason: str | None, observed) -> None:
        self.app.db.record_quote(
            {
                "supplier_id": supplier_id,
                "observed_at": observed.isoformat(),
                "quantity_liters": 1000,
                "status": status,
                "price_per_liter": None,
                "total_price": None,
                "currency": "GBP",
                "source": "test",
                "notes": f"{status} on the last ask",
                "reason": reason,
                "raw_payload": {},
            }
        )

    def test_both_envelopes_split_the_empty_outcomes(self) -> None:
        ids = self._init()
        now = utcnow_naive()
        self._attempt(
            ids["ValueOils"], "manual_action_required", "no_quote_page", now - timedelta(minutes=3)
        )
        self._attempt(ids["Scottish Fuels"], "error", "site_error", now - timedelta(minutes=2))

        prices = self.app.current_prices()

        self.assertEqual(
            [row["supplier_name"] for row in prices["no_quote_suppliers"]], ["ValueOils"]
        )
        self.assertEqual(prices["no_quote_suppliers"][0]["reason"], "no_quote_page")
        self.assertEqual(
            [row["supplier_name"] for row in prices["failed_suppliers"]], ["Scottish Fuels"]
        )
        self.assertEqual(prices["failed_suppliers"][0]["reason"], "site_error")
        # Neither list holds a supplier that did give a price, and the supplier
        # never asked appears in neither — it is `never_quoted`'s business.
        self.assertEqual(prices["quotes"], [])
        self.assertEqual([row["supplier_name"] for row in prices["never_quoted"]], ["Scottish Fuels Depot"])

        # A run is read back with `status`, so the same two groups are there.
        report = self.app.status()
        self.assertEqual(
            [row["supplier_name"] for row in report["no_quote_suppliers"]], ["ValueOils"]
        )
        self.assertEqual(
            [row["supplier_name"] for row in report["failed_suppliers"]], ["Scottish Fuels"]
        )


if __name__ == "__main__":
    unittest.main()
