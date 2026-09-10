"""The contact-only supplier connectors.

Oilfast, Brogan Fuels, Rix, Scottish Fuels and Regency Oils have no price page to
fetch: each returns a manual quote carrying the supplier's contact details. They
used to build an unused ``httpx.Client`` in ``__init__``; these tests pin that
they do not, because ``get_connector_for_supplier`` constructs a connector per
call and an unclosed pool would leak on every lookup.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from oilwatch.connectors.suppliers.brogan_fuels import BroganFuelsConnector
from oilwatch.connectors.suppliers.oilfast import OilfastConnector
from oilwatch.connectors.suppliers.regency_oils import RegencyOilsConnector
from oilwatch.connectors.suppliers.rix import RixConnector
from oilwatch.connectors.suppliers.scottish_fuels import ScottishFuelsConnector

SUPPLIER = {
    "id": 9,
    "name": "Test Supplier",
    "website": "https://example.test",
    "phone": "01234 567890",
}

# connector class, expected QuoteResult.source, a phone number that must reach the notes
MANUAL_CONNECTORS = [
    (OilfastConnector, "oilfast_manual", "01464 635999"),
    (BroganFuelsConnector, "brogan_fuels_manual", "0345 300 8844"),
    (RixConnector, "rix_manual", "01224 455477"),
    (ScottishFuelsConnector, "scottish_fuels_manual", "0345 300 8844"),
    (RegencyOilsConnector, "regency_oils_manual", "0800 838500"),
]


class NoHttpClientTests(unittest.TestCase):
    def test_contact_only_connectors_build_no_http_client(self) -> None:
        for connector_cls, _, _ in MANUAL_CONNECTORS:
            with self.subTest(connector=connector_cls.__name__):
                with patch("httpx.Client") as client:
                    connector = connector_cls()
                client.assert_not_called()
                self.assertIsNone(connector.client)


class ManualQuoteTests(unittest.TestCase):
    def test_quote_is_manual_and_carries_the_contacts(self) -> None:
        for connector_cls, source, phone in MANUAL_CONNECTORS:
            with self.subTest(connector=connector_cls.__name__):
                result = connector_cls().quote(SUPPLIER, 1000, {"postcode": "AB21 0YA"})
                self.assertEqual(result.status, "manual_action_required")
                self.assertEqual(result.source, source)
                self.assertIn(phone, result.notes)
                self.assertIn("1000", result.notes)
                self.assertIsNone(result.price_per_liter)

    def test_place_order_is_manual(self) -> None:
        for connector_cls, _, _ in MANUAL_CONNECTORS:
            with self.subTest(connector=connector_cls.__name__):
                order = connector_cls().place_order(SUPPLIER, 1000, 1.05, {})
                self.assertEqual(order.status, "manual_action_required")
                self.assertIn("1.05", order.notes)


class ContactDetailTests(unittest.TestCase):
    def test_oilfast_payload_carries_the_depot_email(self) -> None:
        payload = OilfastConnector().quote(SUPPLIER, 1000, {}).raw_payload
        self.assertEqual(payload["email"], "insch@oilfast.co.uk")

    def test_brogan_payload_notes_the_redirect(self) -> None:
        payload = BroganFuelsConnector().quote(SUPPLIER, 1000, {}).raw_payload
        self.assertEqual(payload["redirects_to"], "Scottish Fuels (same company)")

    def test_rix_quote_includes_the_postcode(self) -> None:
        result = RixConnector().quote(SUPPLIER, 1000, {"postcode": "AB21 0YA"})
        self.assertIn("AB21 0YA", result.notes)


if __name__ == "__main__":
    unittest.main()
