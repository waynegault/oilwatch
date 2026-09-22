from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from oilwatch.config import load_supplier_registry
from oilwatch.db import Database
from oilwatch.email_parsing import SUPPLIER_DOMAINS, extract_ppl, supplier_fragment_for
from oilwatch.form_submit import SUPPLIER_FORMS
from oilwatch.graph_email import (
    GRAPH_ENDPOINT,
    REQUEST_SUBJECT_PREFIX,
    GraphEmailMonitor,
    request_subject,
    sender_domain_from_email,
)
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

    def test_a_reply_from_the_groups_own_domain_maps_to_the_supplier(self) -> None:
        """Compass answers from a domain one letter shorter than its website.

        A quote requested on 2026-09-22 came back from sales@compassfuel.co.uk,
        which matched nothing, so the reply sat in the inbox with its price
        unread - the same shape as regencyoils.co.uk, mapped for the same reason.
        """
        self.assertEqual(supplier_fragment_for("compassfuel.co.uk"), "compassfuels.co.uk")
        self.assertEqual(supplier_fragment_for("sales.compassfuel.co.uk"), "compassfuels.co.uk")
        self.assertEqual(supplier_fragment_for("compassfuels.co.uk"), "compassfuels.co.uk")

    def test_a_reply_from_turriffs_hyphenated_mail_domain_maps_to_the_supplier(self) -> None:
        """Turriff's mail domain keeps a hyphen its website dropped.

        The contact page carries rory@turriff-fuels.co.uk while the supplier's
        website is turrifffuels.com, so a reply would map nowhere and sit in the
        inbox with its price unread - the same shape as the two entries above.
        """
        self.assertEqual(supplier_fragment_for("turriff-fuels.co.uk"), "turrifffuels.com")
        self.assertEqual(supplier_fragment_for("mail.turriff-fuels.co.uk"), "turrifffuels.com")

    def test_a_domain_that_merely_ends_with_a_known_one_is_not_matched(self) -> None:
        self.assertIsNone(supplier_fragment_for("notvalueoils.com"))
        self.assertIsNone(supplier_fragment_for("example.com"))
        self.assertIsNone(supplier_fragment_for(""))

    def test_every_address_in_the_register_can_be_answered(self) -> None:
        """An address on a register record is a reply domain the sweep must know.

        `submit-requests --by-email` writes to the address on the record, and the
        supplier answers from whatever domain its mail leaves - which no register
        field shows. Regency, Compass, Turriff and Carnegie each replied from a
        domain the map lacked, and each miss was found only after the price had
        sat in the inbox unread, so this reads the register itself rather than
        waiting for the next supplier to answer: the map cannot be derived from
        the register, but a record it could not answer is a defect whether or not
        that supplier has replied yet.
        """
        missing = [
            f"{record.get('name')} <{record['email']}>"
            for record in load_supplier_registry()["suppliers"]
            if record.get("email")
            and supplier_fragment_for(sender_domain_from_email(record["email"])) is None
        ]
        self.assertEqual(
            missing,
            [],
            "these register records carry an address the sweep cannot read a reply "
            "from; add each of those sender domains to SUPPLIER_DOMAINS in "
            "oilwatch/email_parsing.py",
        )

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


class CarnegieFuelsReplyTests(unittest.TestCase):
    """A real reply stating the price as '+ VAT' pence, with the original quoted.

    Carnegie is asked by email because its own site has no form and no published
    price, so this reply is the only price it will ever produce — and on
    2026-09-22 it landed from sales@carnegiefuels.co.uk, a domain the reply map
    did not know, which left it counted as an unrecognised sender with its price
    unread. The quoted request is part of the body here because that is what
    Graph hands the sweep: the subject line and the signature carry numbers of
    their own, and the parse has to survive them.
    """

    BODY = (
        "Hello Wayne,\n\n"
        "Hope you are well.\n\n"
        "The current price for 1000 litres is 114.95ppl + VAT and delivery would "
        "be by Thursday, if that was suitable for you.\n\n"
        "Kind Regards,\n\n"
        "Stevie-Leigh Shannon\nOffice Sales Supervisor\n\n"
        "Carnegie Fuels Limited\n7 West Road\nBrechin Business Park\nBrechin\n"
        "DD9 6RJ\n\n01356 648648\nsales@carnegiefuels.co.uk\n\n"
        "-----Original Message-----\n"
        "From: Wayne Gault <waynegault@msn.com>\n"
        "Sent: 22 September 2026 14:19\n"
        "To: Info <info@carnegiefuels.co.uk>\n"
        "Subject: oilwatch quote request - AB21 0YA - 1000L - 2026-09-22\n\n"
        "Please could you quote for 1000 litres of heating oil (kerosene) "
        "delivered to Hatton of Fintray, Aberdeenshire, Scotland, AB21 0YA.\n\n"
        "Name: Wayne Gault\nEmail: waynegault@msn.com\nPhone: 07720061019\n"
    )

    def test_extracts_the_stated_price_and_uplifts_the_vat(self) -> None:
        """'+ VAT' is ex-VAT: reading it as inclusive would undercut the quote.

        114.95p ex-VAT is 120.70p once the domestic rate is applied, so a parse
        that stored the figure as it stands would report £1149.50 for the order
        the supplier prices at £1206.98.
        """
        self.assertEqual(extract_ppl(self.BODY), 1.1495)
        inc_vat = apply_vat(1.1495, DOMESTIC_VAT_RATE)
        self.assertAlmostEqual(inclusive_total(inc_vat, 1000), 1206.98, delta=0.06)

    def test_the_reply_domain_maps_to_the_supplier(self) -> None:
        self.assertEqual(
            supplier_fragment_for("carnegiefuels.co.uk"), "carnegiefuels.co.uk"
        )
        self.assertEqual(
            supplier_fragment_for("sales.carnegiefuels.co.uk"), "carnegiefuels.co.uk"
        )


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
    #: Mirrors ``config.Settings``, which is where the real threshold lives. The
    #: judgement tests place their probabilities either side of this value, so a
    #: drift here would quietly test a threshold the sweep does not use.
    fuel_mail_min_probability = 0.8


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

    def _sweep(
        self, inbox: list[dict], deleted_items: list[dict], judgement: float | None = None
    ) -> tuple[list[dict], list[str]]:
        """One monitor pass, with these messages visible where indicated.

        The fuel-mail judgement is patched in every pass: it is asked over the
        network, and a sweep here must stay offline. The default of ``None`` is
        what an install with no TypeSafe key gets, so the passes that are not
        about the judgement keep the behaviour they had before it existed.
        """
        deleted_urls: list[str] = []
        # Stashed so a test can ask how often the judgement was asked for: the
        # whole point of caching per sender is that it is asked once, not once
        # per message per sweep.
        self.judge_mock = MagicMock(return_value=judgement)

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
        ), patch("oilwatch.graph_email.fuel_mail_probability", self.judge_mock):
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

    def test_a_quote_no_pattern_can_read_is_named_on_the_judgement(self) -> None:
        """The miss the old test made: fuel mail whose price it cannot parse.

        The naming test was `extract_ppl(...) is not None` — a price regex
        standing in for a judgement about meaning — so a real oil company whose
        quote uses a format no pattern covers read as "not fuel" and was never
        named. That is the single case the alert exists for, and the judgement
        closes it: no price is found here, and the domain is named anyway.
        """
        stranger = self._message("LLL", "inbox-id")
        stranger["from"] = {"emailAddress": {"address": "quotes@unlisted-fuels.example"}}
        stranger["subject"] = "Your heating oil quotation"
        stranger["body"] = {
            "contentType": "text",
            "content": "Kerosene is available on request. See the attached schedule.",
        }

        with self.assertLogs("oilwatch.graph_email", level="INFO") as captured:
            self._sweep([stranger], [], judgement=0.93)

        text = "\n".join(captured.output)
        self.assertIn("fuel quote from unrecognised sender(s): unlisted-fuels.example", text)
        self.assertIn("judged 0.93 likely to be fuel mail", text)

    def test_a_stranger_the_judgement_is_not_sure_about_stays_out_of_the_log(self) -> None:
        """The log is a file on disk, so unsure does not earn a name.

        Eighteen months of the owner's correspondents — a financial ombudsman
        case, NHS Scotland, his bank — went onto one line the first time unknown
        senders were named, which is why the naming test is strict. A probability
        below the threshold is not enough.
        """
        stranger = self._message("MMM", "inbox-id")
        stranger["from"] = {"emailAddress": {"address": "caseworker@ombudsman.example"}}
        stranger["body"] = {
            "contentType": "text",
            "content": "Please find attached our response to your complaint.",
        }

        with self.assertLogs("oilwatch.graph_email", level="INFO") as captured:
            self._sweep([stranger], [], judgement=0.4)

        text = "\n".join(captured.output)
        self.assertNotIn("ombudsman.example", text, "a stranger's domain stays out of the log")
        self.assertIn("1 from unrecognised", text, "and is still counted")

    def test_a_sender_is_judged_once_however_many_messages_it_sends(self) -> None:
        """One request per sender, not one per message, and never twice.

        Unrecognised mail is left in the mailbox rather than deleted, so the same
        messages are re-read every sweep: a judgement per message would spend a
        request on each of them, hourly, forever. Two messages from one sender in
        one pass must produce one call, and the next pass must ask nothing at all
        because the verdict is stored.
        """
        def body(text: str) -> dict:
            return {"contentType": "text", "content": text}

        first = self._message("NNN", "inbox-id")
        first["from"] = {"emailAddress": {"address": "quotes@unlisted-fuels.example"}}
        first["body"] = body("Kerosene is available on request.")
        second = self._message("OOO", "deleted-items-id")
        second["from"] = first["from"]
        second["body"] = body("Our schedule of oils is enclosed.")

        self._sweep([first], [second], judgement=0.9)
        self.assertEqual(self.judge_mock.call_count, 1, "one judgement for the sender")
        stored = self.db.sender_judgement("unlisted-fuels.example")
        assert stored is not None
        self.assertEqual(stored["fuel_probability"], 0.9)

        later = self._message("PPP", "inbox-id")
        later["from"] = first["from"]
        later["body"] = body("A further note about kerosene.")

        with self.assertLogs("oilwatch.graph_email", level="INFO") as captured:
            self._sweep([later], [])

        self.judge_mock.assert_not_called()
        self.assertIn("unlisted-fuels.example", "\n".join(captured.output))

    def test_a_sender_judged_not_fuel_is_asked_again_for_later_mail(self) -> None:
        """A negative verdict is not permanent, because only one of them is useful.

        A sender judged "not fuel" on one message used to stay silent forever, so
        a fuel reply in prose no pattern reads - the exact case this judgement
        exists for - was never even asked about: its sender's earlier newsletter
        had already answered for it. Mail arriving afterwards is judged afresh.
        """
        def body(text: str) -> dict:
            return {"contentType": "text", "content": text}

        newsletter = self._message("RRR", "inbox-id")
        newsletter["from"] = {"emailAddress": {"address": "offers@a-newsletter.example"}}
        newsletter["body"] = body("Our winter brochure is enclosed.")

        with self.assertLogs("oilwatch.graph_email", level="INFO") as first_pass:
            self._sweep([newsletter], [], judgement=0.02)

        self.assertEqual(self.judge_mock.call_count, 1, "asked about the mail it had")
        self.assertNotIn(
            "a-newsletter.example",
            "\n".join(first_pass.output),
            "a verdict below the threshold names nobody",
        )

        # The same sender, later: a quote written as prose, so the price parser
        # finds nothing and only the judgement can name it.
        reply = self._message("SSS", "inbox-id")
        reply["receivedDateTime"] = "2026-09-10T11:30:00Z"
        reply["from"] = newsletter["from"]
        reply["body"] = body(
            "Thanks for your enquiry. We can do a thousand litres at one hundred "
            "and eight pence a litre plus VAT, delivery included."
        )

        with self.assertLogs("oilwatch.graph_email", level="INFO") as captured:
            self._sweep([reply], [], judgement=0.95)

        self.assertEqual(self.judge_mock.call_count, 1, "the later mail is judged")
        text = "\n".join(captured.output)
        self.assertIn("fuel quote from unrecognised sender(s): a-newsletter.example", text)

    def test_a_sender_already_being_named_is_never_asked_again(self) -> None:
        """The other direction stays sticky: a name is not re-bought.

        Asking about a settled sender would spend a request to reach the answer
        already stored and already acted on, so later mail from it is named
        without a call.
        """
        def body(text: str) -> dict:
            return {"contentType": "text", "content": text}

        first = self._message("TTT", "inbox-id")
        first["from"] = {"emailAddress": {"address": "quotes@listed-fuels.example"}}
        first["body"] = body("Kerosene is available on request.")

        self._sweep([first], [], judgement=0.9)

        later = self._message("UUU", "inbox-id")
        later["receivedDateTime"] = "2026-09-11T09:00:00Z"
        later["from"] = first["from"]
        later["body"] = body("A further note about kerosene.")

        with self.assertLogs("oilwatch.graph_email", level="INFO") as captured:
            self._sweep([later], [])

        self.judge_mock.assert_not_called()
        self.assertIn("listed-fuels.example", "\n".join(captured.output))

    def test_mail_a_verdict_already_covers_is_not_asked_again(self) -> None:
        """One request per new message, not one per message per sweep.

        Unrecognised mail is left in the mailbox, so every message is re-read
        every sweep. If a below-threshold verdict did not record which mail it
        accounted for, the whole mailbox would be re-judged hourly - the cost the
        per-sender cache exists to avoid. The same message, seen again, is
        covered; the newer one beside it is not.
        """
        def body(text: str) -> dict:
            return {"contentType": "text", "content": text}

        first = self._message("VVV", "inbox-id")
        first["receivedDateTime"] = "2026-09-10T09:00:00Z"
        first["from"] = {"emailAddress": {"address": "hello@a-shop.example"}}
        first["body"] = body("Your order has been dispatched.")

        self._sweep([first], [], judgement=0.01)

        later = self._message("WWW", "inbox-id")
        later["receivedDateTime"] = "2026-09-10T12:00:00Z"
        later["from"] = first["from"]
        later["body"] = body("Another dispatch note, later the same day.")

        # Both are visible now: the one already judged, and the newer one.
        self._sweep([later, first], [], judgement=0.02)

        self.assertEqual(
            self.judge_mock.call_count, 1, "only the mail the verdict did not cover"
        )

    def test_a_readable_price_never_asks_the_judgement(self) -> None:
        """The free test runs first; the judgement is only for what it misses.

        `extract_ppl` settles most fuel-shaped mail exactly and at no cost, so
        asking a model about it as well would be a request spent to learn what
        the regex already said.
        """
        stranger = self._message("QQQ", "inbox-id")
        stranger["from"] = {"emailAddress": {"address": "quotes@unlisted-fuels.example"}}
        stranger["body"] = {"contentType": "text", "content": "Kerosene 99.15p (Excl. VAT)"}

        self._sweep([stranger], [])

        self.judge_mock.assert_not_called()
        self.assertIsNone(self.db.sender_judgement("unlisted-fuels.example"))

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


class QuoteRequestEmailTests(unittest.TestCase):
    """Asking a supplier by email: the marked subject, the payload, and refusals."""

    def _monitor(self) -> GraphEmailMonitor:
        # A client id keeps the constructor off config/settings.json, which a test
        # has no business reading.
        return GraphEmailMonitor(client_id="test-client")

    def test_the_subject_carries_the_marker_and_what_is_being_asked(self) -> None:
        """The marker belongs in the subject, because that is what a reply keeps.

        A body is re-wrapped, quoted and signed under before anyone searches it;
        "Re: <subject>" survives all three, so one search finds every request this
        install has made and every one still owed.
        """
        subject = request_subject("AB21 0YA", 1000, on=datetime(2026, 9, 22, 12, 0))

        self.assertTrue(subject.startswith(REQUEST_SUBJECT_PREFIX))
        self.assertIn("AB21 0YA", subject)
        self.assertIn("1000L", subject)
        self.assertIn("2026-09-22", subject)

    def test_the_message_goes_to_the_supplier_and_is_kept_in_sent_items(self) -> None:
        monitor = self._monitor()
        with (
            patch.object(GraphEmailMonitor, "get_token", return_value={"access_token": "t"}),
            patch("httpx.post", return_value=httpx.Response(202)) as post,
        ):
            monitor.send("info@carnegiefuels.co.uk", "a subject", "a body")

        self.assertEqual(post.call_args.args[0], f"{GRAPH_ENDPOINT}/me/sendMail")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(
            payload["message"]["toRecipients"],
            [{"emailAddress": {"address": "info@carnegiefuels.co.uk"}}],
        )
        self.assertEqual(payload["message"]["subject"], "a subject")
        # Kept, so a request can be checked against the reply it produced.
        self.assertTrue(payload["saveToSentItems"])

    def test_a_refused_send_says_what_graph_said(self) -> None:
        """A send is deliberate, so it raises rather than reading as done.

        The caller reports per supplier; a swallowed refusal would look like a
        request that went out.
        """
        monitor = self._monitor()
        refused = httpx.Response(403, text="Insufficient privileges to complete the operation.")
        with (
            patch.object(GraphEmailMonitor, "get_token", return_value={"access_token": "t"}),
            patch("httpx.post", return_value=refused),
            self.assertRaises(RuntimeError) as raised,
        ):
            monitor.send("info@carnegiefuels.co.uk", "a subject", "a body")

        self.assertIn("403", str(raised.exception))
        self.assertIn("Insufficient privileges", str(raised.exception))

    def test_an_unsigned_in_mailbox_says_how_to_sign_in(self) -> None:
        monitor = self._monitor()
        with (
            patch.object(GraphEmailMonitor, "get_token", return_value=None),
            self.assertRaises(RuntimeError) as raised,
        ):
            monitor.send("info@carnegiefuels.co.uk", "a subject", "a body")

        self.assertIn("login-email", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
