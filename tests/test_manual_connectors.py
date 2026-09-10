"""The contact-only supplier connectors.

Oilfast and Brogan Fuels have no price page to fetch, so their connectors return
a manual quote carrying the contact details. They used to build an unused
``httpx.Client`` in ``__init__``; these tests pin that they do not, since the
registry constructs a connector per call and an unclosed pool would leak.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from oilwatch.connectors.suppliers.brogan_fuels import BroganFuelsConnector
from oilwatch.connectors.suppliers.oilfast import OilfastConnector

OILFAST = {"id": 9, "name": "Oilfast", "website": "https://oilfast.co.uk", "phone": "01464 635999"}
BROGAN = {"id": 10, "name": "Brogan Fuels", "website": "https://www.brogans.co.uk", "phone": "0345 300 8844"}


class NoHttpClientTests(unittest.TestCase):
    def test_oilfast_builds_no_http_client(self) -> None:
        with patch("httpx.Client") as client:
            OilfastConnector()
        client.assert_not_called()

    def test_brogan_builds_no_http_client(self) -> None:
        with patch("httpx.Client") as client:
            BroganFuelsConnector()
        client.assert_not_called()


class OilfastConnectorTests(unittest.TestCase):
    def test_quote_is_manual_with_contact_details(self) -> None:
        result = OilfastConnector().quote(OILFAST, 1000, {"postcode": "AB21 0YA"})
        self.assertEqual(result.status, "manual_action_required")
        self.assertEqual(result.source, "oilfast_manual")
        self.assertIn("01464 635999", result.notes)
        self.assertIn("1000L", result.notes)
        self.assertEqual(result.raw_payload["email"], "insch@oilfast.co.uk")

    def test_place_order_is_manual(self) -> None:
        order = OilfastConnector().place_order(OILFAST, 1000, 1.05, {})
        self.assertEqual(order.status, "manual_action_required")
        self.assertIn("1.05", order.notes)


class BroganFuelsConnectorTests(unittest.TestCase):
    def test_quote_is_manual_with_contact_details(self) -> None:
        result = BroganFuelsConnector().quote(BROGAN, 1000, {})
        self.assertEqual(result.status, "manual_action_required")
        self.assertEqual(result.source, "brogan_fuels_manual")
        self.assertIn("0345 300 8844", result.notes)
        self.assertEqual(result.raw_payload["redirects_to"], "Scottish Fuels (same company)")

    def test_place_order_is_manual(self) -> None:
        order = BroganFuelsConnector().place_order(BROGAN, 1000, 1.05, {})
        self.assertEqual(order.status, "manual_action_required")
        self.assertIn("1.05", order.notes)


if __name__ == "__main__":
    unittest.main()
