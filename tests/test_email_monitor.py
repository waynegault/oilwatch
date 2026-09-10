from __future__ import annotations

import email
import os
import unittest
from unittest.mock import patch

from oilwatch.email_monitor import SUPPLIER_DOMAINS, extract_ppl, load_email_config, sender_domain
from oilwatch.form_submit import SUPPLIER_FORMS
from oilwatch.graph_email import GraphEmailMonitor, sender_domain_from_email
from oilwatch.identity import Contact


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


if __name__ == "__main__":
    unittest.main()
