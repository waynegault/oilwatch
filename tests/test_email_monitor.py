from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oilwatch.db import Database
from oilwatch.email_parsing import SUPPLIER_DOMAINS, extract_ppl, supplier_fragment_for
from oilwatch.form_submit import SUPPLIER_FORMS
from oilwatch.graph_email import GraphEmailMonitor, sender_domain_from_email
from oilwatch.pricing import DOMESTIC_VAT_RATE, apply_vat, inclusive_total


class ExtractPplTests(unittest.TestCase):
    def test_pence_per_litre(self) -> None:
        self.assertEqual(extract_ppl("Heating oil: 101.03p per litre (Excl. VAT)"), 1.0103)

    def test_pence_excl_vat(self) -> None:
        self.assertEqual(extract_ppl("Kerosene 99.15p (Excl. VAT)"), 0.9915)

    def test_pounds_per_litre(self) -> None:
        self.assertEqual(extract_ppl("Our price is £1.01 per litre"), 1.01)

    def test_no_price(self) -> None:
        self.assertIsNone(extract_ppl("Thanks for your enquiry, we'll be in touch."))

    def test_fuelsoft_four_decimal_pounds_takes_cheapest(self) -> None:
        text = "(3302) HEATING OIL (KEROSENE) 1000 £1.0481 £1048.10 ... £1.0781 £1078.10"
        self.assertEqual(extract_ppl(text), 1.0481)

    def test_rix_price_per_litre_pence_no_unit(self) -> None:
        text = "1000 litres of Kerosene Price per litre 110.35 Total cost £1158.68"
        self.assertEqual(extract_ppl(text), 1.1035)

    def test_scottish_fuels_price_per_litre_p(self) -> None:
        self.assertEqual(extract_ppl("Price Per Litre (Excl. VAT): 101.03p"), 1.0103)

    def test_valueoils_quote_states_pence_beside_the_total(self) -> None:
        """ValueOils puts the unit price as bare pence next to the option total.

        No "per litre" wording and no Excl. VAT suffix, so the other patterns
        missed it and the quote email stayed unprocessed in the inbox.
        """
        text = (
            "Delivery Option Fuel PPL Ex. VAT Total You Pay "
            "Standard Delivery - Estimated Delivery by Wednesday 23rd Sep 2026 "
            "102.90p £1,101.45 Buy Now "
            "Express Delivery 4 (+£69.90) 109.70p £1,242.75 Buy Now"
        )
        self.assertEqual(extract_ppl(text), 1.029)

    def test_valueoils_standard_total_beats_the_pence_beside_it(self) -> None:
        """The email's Standard Delivery total includes VAT and the commission.

        The pence figure beside it excludes both, so reading the pence undercut
        the browser connector that reads the real total (£1,187.55, not the
        pence-based £1,177.10).
        """
        text = (
            "Order Quantity: 1000 Litres Heating Oil Quote: All Delivery Options "
            "Delivery Option Fuel PPL Ex. VAT Total You Pay "
            "Standard Delivery - Estimated Delivery by Tuesday 29th Sep 2026 "
            "115.10p £1,229.55 Buy Now "
            "Express Delivery 7 (+£29.90) 115.10p £1,259.45 Buy Now"
        )
        # £1,229.55 for 1000L inc VAT+commission -> 1.1710/L ex-VAT equivalent,
        # which the caller uplifts back to ~£1.2295/L.
        ppl = extract_ppl(text)
        assert ppl is not None
        self.assertAlmostEqual(ppl, 1.171, places=4)


class SupplierMappingsTests(unittest.TestCase):
    def test_supplier_domains_present(self) -> None:
        self.assertIn("gleaner.co.uk", SUPPLIER_DOMAINS)
        self.assertIn("oilfast.co.uk", SUPPLIER_DOMAINS)
        # Domains actually seen on reply emails (not just the marketing sites).
        self.assertIn("regencyoils.co.uk", SUPPLIER_DOMAINS)
        self.assertIn("certasenergy.co.uk", SUPPLIER_DOMAINS)
        self.assertIn("connon-oils.co.uk", SUPPLIER_DOMAINS)

    def test_reply_domain_maps_to_correct_fragment(self) -> None:
        self.assertEqual(SUPPLIER_DOMAINS["regencyoils.co.uk"], "regencyoils.com")
        self.assertEqual(SUPPLIER_DOMAINS["certasenergy.co.uk"], "scottishfuels.co.uk")
        self.assertEqual(SUPPLIER_DOMAINS["connon-oils.co.uk"], "connon")

    def test_a_sender_subdomain_is_recognised(self) -> None:
        """Mail-outs come from a subdomain while quotes use the bare domain.

        BoilerJuice sends news from ``e.boilerjuice.com``; an exact map skipped
        it, so the mail was neither parsed nor cleared.
        """
        self.assertEqual(supplier_fragment_for("e.boilerjuice.com"), "boilerjuice.com")
        self.assertEqual(supplier_fragment_for("E.BOILERJUICE.COM"), "boilerjuice.com")
        self.assertEqual(supplier_fragment_for("boilerjuice.com"), "boilerjuice.com")

    def test_a_domain_that_merely_ends_with_a_known_one_is_not_matched(self) -> None:
        self.assertIsNone(supplier_fragment_for("notvalueoils.com"))
        self.assertIsNone(supplier_fragment_for("example.com"))
        self.assertIsNone(supplier_fragment_for(""))

    def test_body_text_strips_html(self) -> None:
        msg = {
            "body": {
                "contentType": "html",
                "content": "<html><body>Price per litre <b>101.03p</b></body></html>",
            }
        }
        self.assertIn("101.03p", GraphEmailMonitor._body_text(msg))

    def test_body_text_falls_back_to_preview(self) -> None:
        msg = {"bodyPreview": "fallback preview"}
        self.assertEqual(GraphEmailMonitor._body_text(msg), "fallback preview")

    def test_sender_domain_from_email(self) -> None:
        self.assertEqual(sender_domain_from_email("quotes@gleaner.co.uk"), "gleaner.co.uk")
        self.assertEqual(sender_domain_from_email("John <sales@oilfast.co.uk>"), "oilfast.co.uk")
        self.assertEqual(sender_domain_from_email(""), "")

    def test_forms_configured(self) -> None:
        self.assertIn("gleaner_oils", SUPPLIER_FORMS)
        self.assertIn("oilfast", SUPPLIER_FORMS)
        self.assertIn("highland_fuels", SUPPLIER_FORMS)


class HighlandFuelsReplyTests(unittest.TestCase):
    """A real reply stating the price as 'PPL' plus an inc-VAT order total."""

    BODY = (
        "Good morning,\n\n"
        "Please see todays quote below,\n\n"
        "1000L – 107.50PPL – Total Inc VAT is £1128.75\n\n"
        "Kind regards\nJames"
    )

    def test_extracts_the_ppl_price_ex_vat(self) -> None:
        self.assertEqual(extract_ppl(self.BODY), 1.075)

    def test_ppl_alone_is_understood(self) -> None:
        self.assertEqual(extract_ppl("107.50PPL"), 1.075)

    def test_the_stated_total_cross_checks(self) -> None:
        """The derived total lands on the supplier's own, to within rounding.

        107.50p ex-VAT over 1000L plus 5% is £1128.75. The pipeline stores the
        per-litre price rounded to 4 decimals, so a 1000L total can differ by up
        to 5p. That tolerance still proves the parse is right — reading pence as
        pounds or applying VAT twice would be out by orders of magnitude.
        """
        ex_vat = extract_ppl(self.BODY)
        assert ex_vat is not None
        inc_vat = apply_vat(ex_vat, DOMESTIC_VAT_RATE)
        self.assertAlmostEqual(inclusive_total(inc_vat, 1000), 1128.75, delta=0.06)


class BoilerJuiceQuoteTests(unittest.TestCase):
    """BoilerJuice states an inclusive total, with a service charge in it.

    1,000L for £1588.99 is 1.58899/L inc VAT. Recording the headline "Price per
    litre 150.0 ppl" instead would understate the real cost by the £13.99
    service charge, which appears nowhere else in the message.
    """

    BODY = (
        "Here is our cheapest quote. Hurry, as your quote is only valid for 30 "
        "minutes. Get 1,000 litres of kerosene 28 for £1588.99 Price per litre "
        "150.0 ppl Order Now Tue 31st Mar 156.5 PPL You pay £1657.24"
    )

    def test_uses_the_total_you_pay_not_the_headline_ppl(self) -> None:
        ppl = extract_ppl(self.BODY)
        assert ppl is not None
        self.assertAlmostEqual(ppl, 1.58899 / 1.05, places=4)

    def test_the_derived_total_reproduces_the_suppliers_own(self) -> None:
        ex_vat = extract_ppl(self.BODY)
        assert ex_vat is not None
        inc_vat = apply_vat(ex_vat, DOMESTIC_VAT_RATE)
        self.assertAlmostEqual(inclusive_total(inc_vat, 1000), 1588.99, delta=0.06)

    def test_the_sender_domain_is_recognised(self) -> None:
        self.assertEqual(SUPPLIER_DOMAINS["boilerjuice.com"], "boilerjuice.com")


class _Response:
    """Just enough of an httpx.Response for the monitor's calls."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _Settings:
    quote_quantity_liters = 1000
    currency = "GBP"


class _App:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.settings = _Settings()


class GraphRequestHeaderTests(unittest.TestCase):
    def test_requests_pin_immutable_message_ids(self) -> None:
        """Without this, deleting a reply changes its id and it is mined again."""
        monitor = GraphEmailMonitor(client_id="00000000-0000-0000-0000-000000000000")
        headers = monitor._headers({"access_token": "secret"})
        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertIn("ImmutableId", headers["Prefer"])


class GraphSweepRerunTests(unittest.TestCase):
    """A reply must not be recorded twice when its id changes on a folder move.

    The monitor deletes a processed reply out of the inbox, which moves it to
    Deleted Items, and Graph hands the moved copy a *different* id. The next
    sweep therefore reads it as unseen mail, and on 2026-09-10 that produced
    seven duplicate quotes and three duplicate discount codes.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.sqlite")
        self.db.init_schema()
        self.db.upsert_supplier(
            {
                "name": "Rix",
                "website": "https://rix.co.uk/homes/fuel-quote",
                "status": "active",
                "connector_type": "manual",
                "connector_config": {},
            }
        )
        self.app = _App(self.db)
        self.monitor = GraphEmailMonitor(client_id="00000000-0000-0000-0000-000000000000")
        self._get_patcher = patch.object(GraphEmailMonitor, "get_token")
        self._get_patcher.start().return_value = {"access_token": "token", "refresh_token": ""}
        self.addCleanup(self._get_patcher.stop)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _message(message_id: str, folder: str, received: str = "2026-09-10T09:22:18Z") -> dict:
        return {
            "id": message_id,
            "parentFolderId": folder,
            "receivedDateTime": received,
            "from": {"emailAddress": {"address": "sales@rix.co.uk"}},
            "subject": "Your latest heating oil quote from Rix",
            "body": {"contentType": "text", "content": "Price per litre 110.35 Total cost £1158.68"},
        }

    def _sweep(self, inbox: list[dict], deleted_items: list[dict]) -> tuple[list[dict], list[str]]:
        """One monitor pass, with these messages visible where indicated."""
        deleted_urls: list[str] = []

        def fake_get(url: str, **kwargs: object) -> _Response:
            if url.endswith("/me/mailFolders/inbox"):
                return _Response({"id": "inbox-id"})
            if "recoverableitemsdeletions" in url:
                return _Response({"value": deleted_items})
            return _Response({"value": inbox})

        def fake_delete(url: str, **kwargs: object) -> _Response:
            deleted_urls.append(url)
            return _Response({})

        with patch("oilwatch.graph_email.httpx.get", side_effect=fake_get), patch(
            "oilwatch.graph_email.httpx.delete", side_effect=fake_delete
        ):
            recorded = self.monitor.run(self.app)
        return recorded, deleted_urls

    def test_a_reply_mined_twice_is_recorded_and_reported_once(self) -> None:
        first, deleted = self._sweep([self._message("AAA", "inbox-id")], [])
        self.assertEqual(len(first), 1)
        self.assertEqual(len(self.db.all_quotes()), 1)
        self.assertEqual(len(deleted), 1, "the processed reply is deleted from the inbox")

        # The moved copy carries a new id, so the ledger cannot recognise it.
        again, _ = self._sweep([], [self._message("BBB", "deleted-items-id")])
        self.assertEqual(again, [], "the same observation must not be reported again")
        self.assertEqual(len(self.db.all_quotes()), 1, "and must not be stored again")

    def test_a_genuinely_new_quote_is_still_recorded(self) -> None:
        self._sweep([self._message("AAA", "inbox-id")], [])

        later, _ = self._sweep(
            [], [self._message("CCC", "deleted-items-id", received="2026-09-10T10:22:18Z")]
        )
        self.assertEqual(len(later), 1)
        self.assertEqual(len(self.db.all_quotes()), 2)

    def test_a_repeat_discount_code_is_not_stored_twice(self) -> None:
        body = "£10 OFF 500-999 litres - Code: UWCNI154305"
        message = self._message("DDD", "inbox-id")
        message["body"] = {"contentType": "text", "content": body}

        self._sweep([message], [])
        moved = self._message("EEE", "deleted-items-id")
        moved["body"] = {"contentType": "text", "content": body}
        self._sweep([], [moved])

        self.assertEqual(len(self.db.active_discounts()), 1)

    def test_supplier_mail_outside_the_inbox_still_says_what_it_did(self) -> None:
        """The third silent path, found by classifying the live mailbox.

        These log lines used to sit inside the branch that deletes, so a supplier
        newsletter already in Deleted Items was re-scanned every sweep without a
        word: the same `scanned 187 … 186 from unrecognised` appeared hourly with
        one message unexplained. The message is named whether or not it is in the
        inbox, and the wording says which happened to it.
        """
        message = self._message("KKK", "deleted-items-id")
        message["subject"] = "Feedback Friday"
        message["body"] = {"contentType": "text", "content": "Prices are volatile."}

        with self.assertLogs("oilwatch.graph_email", level="INFO") as captured:
            recorded, deleted = self._sweep([], [message])

        text = "\n".join(captured.output)
        self.assertEqual(recorded, [])
        self.assertEqual(deleted, [], "nothing outside the inbox is deleted")
        self.assertIn("Feedback Friday", text)
        self.assertIn("left where it is", text)

    def test_a_newsletter_is_cleared_but_not_marked_processed(self) -> None:
        """Supplier mail with nothing in it is swept out, not left to pile up.

        Parsing always runs before this, so a real deal is never dropped by the
        clearing, and the message is left unmarked so a later parser improvement
        can still reach it in Deleted Items.
        """
        message = self._message("FFF", "inbox-id")
        message["subject"] = "Have you been asking the million-dollar question?"
        message["body"] = {
            "contentType": "text",
            "content": "Prices remain volatile. Check today's price.",
        }

        recorded, deleted = self._sweep([message], [])

        self.assertEqual(recorded, [])
        self.assertEqual(len(deleted), 1, "cleared out of the inbox")
        self.assertFalse(self.db.message_processed("FFF"), "left re-scannable")

    def test_the_sweep_says_what_it_recorded(self) -> None:
        """A captured reply has to leave a trace, not just a row in the database.

        Recording a quote logged nothing, so an unattended sweep that captured
        Rix's email and one that found nothing wrote the same empty log: on
        2026-09-18 data/oilwatch.log was 0 bytes back to the 13th while sweeps
        ran hourly and recorded Rix, Regency and Scottish Fuels replies.
        """
        with self.assertLogs("oilwatch.graph_email", level="INFO") as captured:
            self._sweep([self._message("GGG", "inbox-id")], [])

        text = "\n".join(captured.output)
        expected = apply_vat(1.1035, DOMESTIC_VAT_RATE)
        self.assertIn(f"recorded {expected:.4f}/L from Rix (rix.co.uk)", text)
        self.assertIn("sweep: scanned 1, recorded 1, 0 from unrecognised", text)

    def test_a_personal_sender_the_map_does_not_know_is_counted_but_not_named(self) -> None:
        """Naming unknown senders leaked the owner's inbox into a log file.

        The first cut listed every unrecognised domain: one sweep put eighty of
        them on one line, including a financial ombudsman case, NHS Scotland and
        his bank, and named no supplier at all. The count stays, because it says
        the sweep ran and how much it skipped.
        """
        stranger = self._message("HHH", "inbox-id")
        stranger["from"] = {"emailAddress": {"address": "caseworker@ombudsman.example"}}
        stranger["subject"] = "Your complaint reference"
        stranger["body"] = {
            "contentType": "text",
            "content": "Please find attached our response to your complaint. No price, no quote.",
        }

        with self.assertLogs("oilwatch.graph_email", level="INFO") as captured:
            self._sweep([stranger], [])

        text = "\n".join(captured.output)
        self.assertIn("sweep: scanned 1, recorded 0, 1 from unrecognised", text)
        self.assertNotIn("ombudsman.example", text, "a stranger's domain stays out of the log")
        self.assertNotIn("Your complaint reference", text)

    def test_fuel_shaped_mail_from_an_unknown_sender_is_named(self) -> None:
        """The one unknown sender worth naming: an oil company being missed.

        A reply that reads like a quote but comes from a domain missing from
        SUPPLIER_DOMAINS is skipped, so naming the domain is the only way to
        notice it and add it.
        """
        stranger = self._message("III", "inbox-id")
        stranger["from"] = {"emailAddress": {"address": "quotes@unlisted-fuels.example"}}
        stranger["subject"] = "Your heating oil quotation"
        stranger["body"] = {"contentType": "text", "content": "Kerosene 99.15p (Excl. VAT)"}

        with self.assertLogs("oilwatch.graph_email", level="INFO") as captured:
            self._sweep([stranger], [])

        text = "\n".join(captured.output)
        self.assertIn("fuel quote from unrecognised sender(s): unlisted-fuels.example", text)
        self.assertIn("add the domain to SUPPLIER_DOMAINS", text)
        self.assertNotIn("Your heating oil quotation", text, "the subject stays out of the log")

    def test_a_known_domain_with_no_supplier_row_is_named_rather_than_dropped(self) -> None:
        """The second silent path, found by arithmetic on a live sweep.

        A sweep logged `scanned 187 … 186 from unrecognised sender(s)` — so one
        message was neither unrecognised nor accounted for by any line, because
        two branches dropped mail without a word: a domain that maps to a
        supplier fragment no row carries, and a duplicate observation. Which of
        them had taken it was unknowable from the log, and that is the fault.
        """
        message = self._message("JJJ", "inbox-id")
        # Maps to scottishfuels.co.uk in SUPPLIER_DOMAINS; this fixture has a row
        # for Rix only, so there is nowhere to file it.
        message["from"] = {"emailAddress": {"address": "no-reply@certasenergy.co.uk"}}

        with self.assertLogs("oilwatch.graph_email", level="WARNING") as captured:
            recorded, _ = self._sweep([message], [])

        text = "\n".join(captured.output)
        self.assertEqual(recorded, [])
        self.assertIn("certasenergy.co.uk", text)
        self.assertIn("scottishfuels.co.uk", text)

    def test_a_duplicate_observation_says_so_rather_than_vanishing(self) -> None:
        """The other silent branch: expected, but it has to be visible.

        A reply whose id changed on a folder move is recorded once and reported
        once — and the second sighting now says what it was, so a sweep's counts
        add up instead of one message disappearing.
        """
        self._sweep([self._message("AAA", "inbox-id")], [])
        with self.assertLogs("oilwatch.graph_email", level="INFO") as captured:
            again, _ = self._sweep([], [self._message("BBB", "deleted-items-id")])

        text = "\n".join(captured.output)
        self.assertEqual(again, [])
        self.assertIn("already recorded", text)


if __name__ == "__main__":
    unittest.main()
