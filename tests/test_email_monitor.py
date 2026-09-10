from __future__ import annotations

import email
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oilwatch.db import Database
from oilwatch.email_monitor import SUPPLIER_DOMAINS, extract_ppl, load_email_config, sender_domain
from oilwatch.form_submit import SUPPLIER_FORMS
from oilwatch.graph_email import GraphEmailMonitor, sender_domain_from_email
from oilwatch.identity import Contact
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


class SenderDomainTests(unittest.TestCase):
    def test_extracts_domain(self) -> None:
        msg = email.message_from_string("From: John <quotes@gleaner.co.uk>\nSubject: Quote\n\nbody")
        self.assertEqual(sender_domain(msg), "gleaner.co.uk")

    def test_missing_from(self) -> None:
        msg = email.message_from_string("Subject: no from\n\nbody")
        self.assertEqual(sender_domain(msg), "")


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


class LoadEmailConfigTests(unittest.TestCase):
    def test_reads_password_from_env(self) -> None:
        env = {"MICROSOFT_PASSWORD": "secret123", "MICROSOFT_EMAIL": "owner@example.com"}
        with patch.dict(os.environ, env, clear=False):
            config = load_email_config()
        self.assertEqual(config["email"], "owner@example.com")
        self.assertEqual(config["password"], "secret123")
        self.assertEqual(config["imap_server"], "outlook.office365.com")
        self.assertEqual(config["imap_port"], 993)

    def test_mailbox_defaults_to_configured_contact(self) -> None:
        """With no MICROSOFT_EMAIL the mailbox comes from the contact, not source."""
        env = {"MICROSOFT_PASSWORD": "secret123", "MICROSOFT_EMAIL": ""}
        with patch.dict(os.environ, env, clear=False), patch(
            "oilwatch.email_monitor.load_contact",
            return_value=Contact(email="owner@example.com"),
        ):
            config = load_email_config()
        self.assertEqual(config["email"], "owner@example.com")

    def test_env_overrides_defaults(self) -> None:
        env = {
            "MICROSOFT_PASSWORD": "pw",
            "MICROSOFT_EMAIL": "other@example.com",
            "MICROSOFT_IMAP_SERVER": "imap.example.com",
            "MICROSOFT_IMAP_PORT": "123",
        }
        with patch.dict(os.environ, env, clear=False):
            config = load_email_config()
        self.assertEqual(config["email"], "other@example.com")
        self.assertEqual(config["password"], "pw")
        self.assertEqual(config["imap_server"], "imap.example.com")
        self.assertEqual(config["imap_port"], 123)


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


if __name__ == "__main__":
    unittest.main()
