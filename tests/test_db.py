from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
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

    def test_max_age_window_excludes_stale_rows_and_names_them(self) -> None:
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

        # Its mirror names what the window dropped, with the last price it gave,
        # so a thin result can be explained instead of looking like a failure.
        excluded = self.db.stale_quotes(max_age_days=30)
        self.assertEqual([q["supplier_name"] for q in excluded], ["Stale"])
        self.assertEqual(excluded[0]["observed_at"], "2007-01-26")
        self.assertEqual(excluded[0]["price_per_liter"], 0.20)

        # Unbounded there is no window, hence nothing excluded.
        self.assertEqual(self.db.stale_quotes(), [])

    def test_the_window_is_24_hours_not_a_calendar_day(self) -> None:
        """``max_age_days=1`` means a day, not "since midnight".

        A date cutoff kept anything from the previous calendar day, so a quote
        could be up to ~48 hours old and still be offered as current — including
        ones whose own ``valid_until`` had already passed.
        """
        now = utcnow_naive()
        for name, age in (("Recent", timedelta(hours=23)), ("Older", timedelta(hours=25))):
            supplier_id = self.db.upsert_supplier(
                self._supplier(name, f"https://{name.lower()}.example.com")
            )
            self.db.record_quote(
                {
                    "supplier_id": supplier_id,
                    "observed_at": (now - age).isoformat(),
                    "quantity_liters": 1000,
                    "status": "ok",
                    "price_per_liter": 1.0,
                    "total_price": 1000.0,
                    "currency": "GBP",
                    "source": "test",
                    "notes": "",
                    "raw_payload": {},
                }
            )

        windowed = self.db.latest_quotes(max_age_days=1)
        self.assertEqual([q["supplier_name"] for q in windowed], ["Recent"])
        self.assertEqual(
            [q["supplier_name"] for q in self.db.stale_quotes(max_age_days=1)], ["Older"]
        )

    def test_not_refreshed_quotes_names_a_supplier_whose_last_try_failed(self) -> None:
        """A price still inside the window, but not from the latest run.

        Scottish Fuels' shape: a good quote this morning, then an attempt later
        that returned no price. The morning figure still counts, so it has to be
        flagged rather than dropped.
        """
        now = utcnow_naive()
        failed = self.db.upsert_supplier(
            self._supplier("Stale Attempt", "https://stale.example.com")
        )
        fresh = self.db.upsert_supplier(self._supplier("Fresh", "https://fresh.example.com"))
        for supplier_id, when, status, price, notes in (
            (failed, (now - timedelta(hours=2)).isoformat(), "ok", 1.10, ""),
            (
                failed,
                (now - timedelta(hours=1)).isoformat(),
                "manual_action_required",
                None,
                "session expired",
            ),
            (fresh, now.isoformat(), "ok", 1.05, ""),
        ):
            self.db.record_quote(
                {
                    "supplier_id": supplier_id,
                    "observed_at": when,
                    "quantity_liters": 1000,
                    "status": status,
                    "price_per_liter": price,
                    "total_price": None if price is None else price * 1000,
                    "currency": "GBP",
                    "source": "test",
                    "notes": notes,
                    "raw_payload": {},
                }
            )

        rows = self.db.not_refreshed_quotes(max_age_days=1)

        self.assertEqual([r["supplier_name"] for r in rows], ["Stale Attempt"])
        # The figure being compared, and the failed attempt that qualifies it.
        self.assertEqual(rows[0]["price_per_liter"], 1.10)
        self.assertEqual(rows[0]["observed_at"], (now - timedelta(hours=2)).isoformat())
        self.assertEqual(rows[0]["last_attempt_status"], "manual_action_required")
        self.assertEqual(rows[0]["last_attempt_note"], "session expired")

        # It is flagged, not dropped: the price is still offered.
        self.assertIn(
            "Stale Attempt",
            [q["supplier_name"] for q in self.db.latest_quotes(max_age_days=1)],
        )
        # A supplier whose newest row is a success is not flagged.
        self.assertNotIn("Fresh", [r["supplier_name"] for r in rows])
        # Unbounded, there is no window and so nothing to distinguish.
        self.assertEqual(self.db.not_refreshed_quotes(), [])

    def test_mark_missing_suppliers_inactive(self) -> None:
        # Both rows carry the query that found them, i.e. discovery owns them.
        self.db.upsert_supplier(
            self._supplier("A", "https://a.example.com", query="heating oil aberdeenshire")
        )
        self.db.upsert_supplier(
            self._supplier("B", "https://b.example.com", query="heating oil aberdeenshire")
        )
        self.db.mark_missing_suppliers_inactive(["https://a.example.com"])

        suppliers = self.db.list_suppliers(include_inactive=True)
        by_website = {s["website"]: s["status"] for s in suppliers}
        self.assertEqual(by_website["https://a.example.com"], "active")
        self.assertEqual(by_website["https://b.example.com"], "inactive")

    def test_a_register_row_is_never_retired_by_a_discovery_run(self) -> None:
        """The register's suppliers are not search results, so a search cannot drop them.

        ``quote_all`` quotes active rows only, so an unscoped version of this
        marked the whole register inactive on the first ``discover`` — BoilerJuice
        is excluded from discovery on purpose, and a quote page like
        ``gleaner.co.uk/get-a-quote-or-place-an-order/`` is not something a search
        returns. The suppliers the register exists to chase then stopped being
        quoted, silently, until the next ``init`` set them active again.
        """
        self.db.upsert_supplier(self._supplier("Gleaner Oils", "https://www.gleaner.co.uk/"))
        self.db.upsert_supplier(
            self._supplier("Found By Search", "https://found.example.com", query="oil")
        )

        self.db.mark_missing_suppliers_inactive(["https://found.example.com"])

        by_website = {s["website"]: s["status"] for s in self.db.list_suppliers(include_inactive=True)}
        self.assertEqual(by_website["https://www.gleaner.co.uk/"], "active")
        self.assertEqual(by_website["https://found.example.com"], "active")

    def test_a_run_that_found_nothing_retires_only_what_discovery_owns(self) -> None:
        self.db.upsert_supplier(self._supplier("Register Co", "https://register.example.com"))
        self.db.upsert_supplier(
            self._supplier("From A Search", "https://search.example.com", query="oil")
        )

        self.db.mark_missing_suppliers_inactive([])

        by_website = {s["website"]: s["status"] for s in self.db.list_suppliers(include_inactive=True)}
        self.assertEqual(by_website["https://register.example.com"], "active")
        self.assertEqual(by_website["https://search.example.com"], "inactive")

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

    def test_kind_roundtrips_and_defaults_to_supplier(self) -> None:
        """A record that says nothing about `kind` is a supplier, not a blank.

        Fueltool is the one benchmark, and it is a figure rather than a company,
        so the default has to be the honest one for the thirteen other records
        that never mention it.
        """
        self.db.upsert_supplier(self._supplier("Ordinary", "https://ordinary.example.com"))
        self.db.upsert_supplier(
            self._supplier("A Benchmark", "https://bench.example.com", kind="benchmark")
        )

        by_name = {s["name"]: s["kind"] for s in self.db.list_suppliers()}
        self.assertEqual(by_name["Ordinary"], "supplier")
        self.assertEqual(by_name["A Benchmark"], "benchmark")

    def test_record_brent_roundtrip_and_dedup(self) -> None:
        self.assertTrue(self.db.record_brent("2026-03-20", 70.5, "eia"))
        self.assertFalse(self.db.record_brent("2026-03-20", 71.0, "eia"))  # duplicate date ignored

        rows = self.db.all_brent()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["observed_at"], "2026-03-20")
        self.assertEqual(rows[0]["price_usd_per_barrel"], 70.5)
        self.assertEqual(rows[0]["source"], "eia")


class DuplicateObservationTests(unittest.TestCase):
    """Guards against storing the same observation twice."""

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

    def _quote(self, **overrides: object) -> dict:
        record = {
            "supplier_id": self.supplier_id,
            "observed_at": "2026-09-10T09:21:51",
            "quantity_liters": 1000,
            "status": "ok",
            "price_per_liter": 1.1331,
            "total_price": 1133.1,
            "currency": "GBP",
            "source": "email",
            "notes": "",
            "raw_payload": {},
        }
        record.update(overrides)
        return record

    def test_an_identical_quote_is_recognised(self) -> None:
        record = self._quote()
        self.assertFalse(self.db.quote_already_recorded(record))
        self.db.record_quote(record)
        self.assertTrue(self.db.quote_already_recorded(record))

    def test_a_different_price_at_the_same_instant_is_a_new_quote(self) -> None:
        self.db.record_quote(self._quote())
        self.assertFalse(self.db.quote_already_recorded(self._quote(price_per_liter=1.1400)))

    def test_a_different_timestamp_is_a_new_quote(self) -> None:
        self.db.record_quote(self._quote())
        self.assertFalse(self.db.quote_already_recorded(self._quote(observed_at="2026-09-10T10:22:18")))

    def test_the_same_price_from_the_web_is_a_separate_observation(self) -> None:
        self.db.record_quote(self._quote())
        self.assertFalse(self.db.quote_already_recorded(self._quote(source="web")))

    def test_an_identical_discount_is_not_stored_twice(self) -> None:
        offer = {
            "supplier_id": self.supplier_id,
            "code": "UWCNI154305",
            "amount_gbp": 10.0,
            "min_litres": 500,
            "max_litres": 999,
        }
        first = self.db.record_discount(offer)
        second = self.db.record_discount(offer)
        self.assertEqual(first, second, "the repeat should report the row already stored")
        self.assertEqual(len(self.db.active_discounts()), 1)

    def test_a_discount_for_a_different_band_is_stored(self) -> None:
        self.db.record_discount(
            {"supplier_id": self.supplier_id, "code": "X1", "amount_gbp": 10.0, "min_litres": 500, "max_litres": 999}
        )
        self.db.record_discount(
            {"supplier_id": self.supplier_id, "code": "X1", "amount_gbp": 10.0, "min_litres": 1000, "max_litres": 1999}
        )
        self.assertEqual(len(self.db.active_discounts()), 2)

    def test_uncoded_offers_of_different_value_are_not_collapsed(self) -> None:
        """Two offers with no code are only the same offer if they match exactly."""
        self.db.record_discount({"supplier_id": self.supplier_id, "code": None, "amount_gbp": 10.0})
        self.db.record_discount({"supplier_id": self.supplier_id, "code": None, "amount_gbp": 10.0})
        self.db.record_discount({"supplier_id": self.supplier_id, "code": None, "amount_gbp": 12.0})
        self.assertEqual(len(self.db.active_discounts()), 2)

    def test_a_resend_of_the_same_offer_refreshes_its_window(self) -> None:
        """The deadline is most of what a code is worth, so a re-send must refresh it.

        ValueOils re-sent all three of its codes on 2026-09-23 with a fresh 48
        hours. The offers were identical, so the rows matched and the new window
        was dropped with them - leaving the codes expired in ``active_discounts``
        and out of every comparison built on it.
        """
        now = utcnow_naive()
        offer = {
            "supplier_id": self.supplier_id,
            "code": "KJHA154306",
            "amount_gbp": 12.0,
            "min_litres": 1000,
            "max_litres": 1999,
        }
        self.db.record_discount(
            {
                **offer,
                "observed_at": (now - timedelta(days=13)).isoformat(),
                "expires_at": (now - timedelta(days=11)).isoformat(),
            }
        )
        self.assertEqual(self.db.active_discounts(), [], "the first window has lapsed")

        resent = now - timedelta(hours=1)
        self.db.record_discount(
            {
                **offer,
                "observed_at": resent.isoformat(),
                "expires_at": (resent + timedelta(hours=48)).isoformat(),
                "source": "email",
                "terms": "cannot be used in conjunction",
            }
        )

        active = self.db.active_discounts()
        self.assertEqual(len(active), 1, "a re-send refreshes the offer, it does not add one")
        self.assertEqual(active[0]["expires_at"], (resent + timedelta(hours=48)).isoformat())
        self.assertEqual(active[0]["observed_at"], resent.isoformat())
        self.assertEqual(active[0]["terms"], "cannot be used in conjunction")

    def test_an_older_message_cannot_undo_a_newer_window(self) -> None:
        """Old mail must not overwrite what a newer message said.

        The sweep reads the inbox and the recoverable bin together, so a message
        that arrived first can be reached last - and would otherwise restore the
        lapsed window over the live one.
        """
        now = utcnow_naive()
        offer = {
            "supplier_id": self.supplier_id,
            "code": "KJHA154306",
            "amount_gbp": 12.0,
            "min_litres": 1000,
            "max_litres": 1999,
        }
        self.db.record_discount(
            {
                **offer,
                "observed_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=48)).isoformat(),
            }
        )
        self.db.record_discount(
            {
                **offer,
                "observed_at": (now - timedelta(days=13)).isoformat(),
                "expires_at": (now - timedelta(days=11)).isoformat(),
            }
        )

        active = self.db.active_discounts()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["expires_at"], (now + timedelta(hours=48)).isoformat())
        self.assertEqual(active[0]["observed_at"], now.isoformat())

    def test_a_resend_that_states_no_expiry_keeps_the_window_it_replaces(self) -> None:
        """A vaguer re-send must not erase the deadline an earlier one gave.

        A body with no expiry statement parses to ``expires_at`` of ``None``,
        and ``active_discounts`` reads ``None`` as "no expiry given", i.e. never
        expires - so writing it through would resurrect a lapsed code and shade
        it into every comparison built on it, which is the same defect the
        re-send refresh was added to fix, pointing the other way.
        """
        now = utcnow_naive()
        offer = {
            "supplier_id": self.supplier_id,
            "code": "KJHA154306",
            "amount_gbp": 12.0,
            "min_litres": 1000,
            "max_litres": 1999,
        }
        self.db.record_discount(
            {
                **offer,
                "observed_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=48)).isoformat(),
                "terms": "cannot be combined with other offers",
            }
        )
        self.db.record_discount(
            {
                **offer,
                "observed_at": (now + timedelta(hours=1)).isoformat(),
                "expires_at": None,
                "terms": "",
            }
        )

        active = self.db.active_discounts()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["expires_at"], (now + timedelta(hours=48)).isoformat())
        self.assertEqual(active[0]["terms"], "cannot be combined with other offers")
        self.assertEqual(active[0]["observed_at"], (now + timedelta(hours=1)).isoformat())


class QuoteRequestTests(unittest.TestCase):
    """A request stays owed until a price answers it, and only a price does."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.sqlite")
        self.db.init_schema()
        self.supplier_id = self.db.upsert_supplier(
            {
                "name": "Gleaner Oils",
                "website": "https://www.gleaner.co.uk/",
                "status": "active",
                "connector_type": "manual",
                "connector_config": {},
            }
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _quote(self, status: str) -> None:
        self.db.record_quote(
            {
                "supplier_id": self.supplier_id,
                "observed_at": utcnow_naive().isoformat(),
                "quantity_liters": 1000,
                "status": status,
                "currency": "GBP",
                "source": "test",
                "reason": None if status == "ok" else "quote_by_request",
            }
        )

    def _could_be_expected(self) -> list[str]:
        return [row["supplier_name"] for row in self.db.outstanding_quote_requests()]

    def test_a_request_is_owed_until_a_price_from_that_supplier_arrives(self) -> None:
        self.db.record_quote_request(self.supplier_id, "form", postcode="AB00 0AA")
        self.assertEqual(self._could_be_expected(), ["Gleaner Oils"])

        self._quote("ok")

        self.assertEqual(self._could_be_expected(), [])

    def test_an_attempt_that_came_back_with_nothing_leaves_the_request_owed(self) -> None:
        """Only a price answers a request.

        An empty ask is itself recorded in `quotes`, so closing on the appearance
        of a row rather than on a price would retire a request the supplier never
        answered - which is the whole thing this table exists to keep straight.
        """
        self.db.record_quote_request(self.supplier_id, "form")
        self._quote("manual_action_required")
        self.assertEqual(self._could_be_expected(), ["Gleaner Oils"])

    def test_the_longest_owed_request_is_listed_first_with_its_supplier(self) -> None:
        """The oldest first, because that is the one to chase, and named because
        an id alone is not something a reader can act on."""
        other = self.db.upsert_supplier(
            {
                "name": "Oilfast Insch",
                "website": "https://oilfast.co.uk/depot/insch/",
                "status": "active",
                "connector_type": "manual",
                "connector_config": {},
            }
        )
        self.db.record_quote_request(self.supplier_id, "form", requested_at="2026-09-22T09:00:00")
        self.db.record_quote_request(other, "email", requested_at="2026-09-21T09:00:00")

        owed = self.db.outstanding_quote_requests()

        self.assertEqual([row["supplier_name"] for row in owed], ["Oilfast Insch", "Gleaner Oils"])
        self.assertEqual(owed[0]["channel"], "email")


class SenderJudgementTests(unittest.TestCase):
    """What a stored fuel-mail verdict says: the probability, and what it covers."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.sqlite")
        self.db.init_schema()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_a_verdict_records_the_newest_mail_it_accounts_for(self) -> None:
        self.db.record_sender_judgement("a.example", 0.02, "2026-09-22T09:00:00")

        verdict = self.db.sender_judgement("a.example")

        assert verdict is not None
        self.assertEqual(verdict["fuel_probability"], 0.02)
        self.assertEqual(verdict["covers_through"], "2026-09-22T09:00:00")

    def test_the_coverage_only_advances(self) -> None:
        """A verdict must not rewind what it covers, or the sweep re-asks forever.

        Every unrecognised message is re-read on every sweep, so a stored verdict
        whose coverage went backwards would hand the same mail back to the
        judgement hourly - the cost the per-sender cache exists to prevent.
        """
        self.db.record_sender_judgement("a.example", 0.02, "2026-09-22T12:00:00")

        self.db.record_sender_judgement("a.example", 0.03, "2026-09-22T09:00:00")

        verdict = self.db.sender_judgement("a.example")
        assert verdict is not None
        self.assertEqual(verdict["fuel_probability"], 0.03, "the newer verdict stands")
        self.assertEqual(
            verdict["covers_through"],
            "2026-09-22T12:00:00",
            "and what it covered is not forgotten",
        )

    def test_an_unjudged_sender_has_no_verdict(self) -> None:
        self.assertIsNone(self.db.sender_judgement("a.example"))
        self.assertIsNone(self.db.sender_judgement(""))


class EmailedCopyTests(unittest.TestCase):
    """Which row a report uses when a supplier emails a copy of its own quote.

    Four suppliers' quote tools email the quote they have just generated, so one
    quote event leaves two rows: the direct read, and an email row dated from the
    *message* that lands seconds from it. Three of the four carry the same price,
    which is why this went unnoticed for a week — but Rix's copy has been a
    constant 1.3057/L while its quote page reads 1.3267/L, and the copy was
    winning "newest" and being reported as today's price.

    The rule is that inside a minute the direct read is the price, because no
    supplier prices an enquiry by hand that fast. Both rows stay stored: this
    chooses between them, it does not throw one away.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.sqlite")
        self.db.init_schema()
        self.supplier_id = self.db.upsert_supplier(
            {
                "name": "Rix",
                "website": "https://rix.example.com",
                "status": "active",
                "connector_type": "manual",
                "connector_config": {},
            }
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _read(self, observed_at: str, price: float) -> dict:
        """The row a browser quote writes: microsecond timestamp, real source."""
        return {
            "supplier_id": self.supplier_id,
            "observed_at": observed_at,
            "quantity_liters": 1000,
            "status": "ok",
            "price_per_liter": price,
            "total_price": price * 1000,
            "currency": "GBP",
            "source": "rix_browser",
            "notes": "",
            "raw_payload": {},
        }

    def _copy_of(self, observed_at: str, price: float) -> dict:
        """The row the mailbox sweep writes for the supplier's own copy."""
        record = self._read(observed_at, price)
        record["source"] = "email"
        return record

    def test_a_copy_seconds_behind_the_read_does_not_become_the_price(self) -> None:
        """The Rix case, in the shape the live database actually holds it."""
        self.db.record_quote(self._read("2026-09-24T14:40:32.626682", 1.3267))
        self.db.record_quote(self._copy_of("2026-09-24T14:40:34", 1.3057))

        latest = self.db.latest_quotes()

        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["source"], "rix_browser")
        self.assertEqual(latest[0]["price_per_liter"], 1.3267)

    def test_the_copy_is_kept_on_record(self) -> None:
        """The rule picks between two rows; dropping one would lose the figure."""
        self.db.record_quote(self._read("2026-09-24T14:40:32.626682", 1.3267))
        self.db.record_quote(self._copy_of("2026-09-24T14:40:34", 1.3057))

        sources = sorted(quote["source"] for quote in self.db.all_quotes())
        self.assertEqual(sources, ["email", "rix_browser"])

    def test_a_supplier_that_only_answers_by_email_keeps_its_row(self) -> None:
        """Turriff Fuels and Carnegie Fuels have no direct read to be near."""
        self.db.record_quote(self._copy_of("2026-09-24T14:40:34", 1.3057))

        latest = self.db.latest_quotes()

        self.assertEqual([quote["source"] for quote in latest], ["email"])
        self.assertEqual(latest[0]["price_per_liter"], 1.3057)

    def test_an_email_well_after_the_read_is_its_own_observation(self) -> None:
        """A genuine reply must win, so the window cannot be generous.

        Ten minutes is hours of a person's afternoon away from the read; the
        minute exists because no one prices an enquiry by hand inside it.
        """
        self.db.record_quote(self._read("2026-09-24T14:40:32.626682", 1.3267))
        self.db.record_quote(self._copy_of("2026-09-24T14:50:32", 1.2900))

        latest = self.db.latest_quotes()

        self.assertEqual(latest[0]["source"], "email")
        self.assertEqual(latest[0]["price_per_liter"], 1.2900)

    def test_the_stale_read_chooses_the_direct_read_too(self) -> None:
        """``stale_quotes`` names a supplier's last known price, so it must agree.

        If it picked the copy while ``latest_quotes`` picked the read, one
        supplier would read as two different prices in a single report.
        """
        now = utcnow_naive()
        read_at = now - timedelta(days=2)
        self.db.record_quote(self._read(read_at.isoformat(), 1.3267))
        self.db.record_quote(
            self._copy_of((read_at + timedelta(seconds=2)).isoformat(), 1.3057)
        )

        stale = self.db.stale_quotes(max_age_days=1)

        self.assertEqual([row["price_per_liter"] for row in stale], [1.3267])

    def test_the_unrefreshed_read_chooses_the_direct_read_too(self) -> None:
        """Same rule where the latest attempt failed and the price still stands."""
        now = utcnow_naive()
        read_at = now - timedelta(hours=2)
        self.db.record_quote(self._read(read_at.isoformat(), 1.3267))
        self.db.record_quote(
            self._copy_of((read_at + timedelta(seconds=2)).isoformat(), 1.3057)
        )
        self.db.record_quote(
            {
                "supplier_id": self.supplier_id,
                "observed_at": (now - timedelta(hours=1)).isoformat(),
                "quantity_liters": 1000,
                "status": "error",
                "price_per_liter": None,
                "total_price": None,
                "currency": "GBP",
                "source": "rix_browser",
                "notes": "the attempt raised",
                "raw_payload": {},
                "reason": "site_error",
            }
        )

        unrefreshed = self.db.not_refreshed_quotes(max_age_days=1)

        self.assertEqual([row["price_per_liter"] for row in unrefreshed], [1.3267])


if __name__ == "__main__":
    unittest.main()
