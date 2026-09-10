from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from oilwatch.connectors.http_form import HTTPFormConnector
from oilwatch.connectors.manual import ManualConnector
from oilwatch.connectors.price_page import PricePageConnector


def fake_response(text: str, url: str = "https://example.com/prices", status_code: int = 200) -> Mock:
    response = Mock()
    response.text = text
    response.url = url
    response.status_code = status_code
    response.raise_for_status = Mock()
    return response


class ManualConnectorTests(unittest.TestCase):
    def test_quote_requires_manual_action(self) -> None:
        supplier = {"id": 1, "name": "A", "phone": "01224 123456", "email": None, "website": "https://a.example.com"}
        result = ManualConnector().quote(supplier, 1000, {})
        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("01224 123456", result.notes)


class PricePageConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.supplier = {
            "id": 1,
            "name": "A",
            "website": "https://a.example.com",
            "connector_config": {
                "quote_url": "https://a.example.com/prices",
                "price_regex": r"(\d+\.\d{2})\s*p(?:ence)?\s*/\s*l",
            },
        }

    def test_quote_normalises_pence(self) -> None:
        with patch("oilwatch.connectors.price_page.httpx.Client") as Client:
            Client.return_value.get.return_value = fake_response("Kerosene 155.80p/L delivered today")
            result = PricePageConnector().quote(self.supplier, 1000, {})
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.price_per_liter, 1.558)
        self.assertEqual(result.total_price, 1558.0)

    def test_quote_applies_vat_when_configured(self) -> None:
        self.supplier["connector_config"]["vat_rate"] = 0.05
        with patch("oilwatch.connectors.price_page.httpx.Client") as Client:
            Client.return_value.get.return_value = fake_response("Kerosene 155.80p/L delivered today")
            result = PricePageConnector().quote(self.supplier, 1000, {})
        self.assertEqual(result.price_per_liter, 1.6359)
        self.assertEqual(result.total_price, 1635.9)

    def test_missing_regex_raises(self) -> None:
        self.supplier["connector_config"].pop("price_regex")
        connector = PricePageConnector()
        with self.assertRaises(ValueError):
            connector.quote(self.supplier, 1000, {})


class HTTPFormConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.supplier = {
            "id": 1,
            "name": "A",
            "website": "https://a.example.com",
            "connector_config": {
                "quote_url": "https://a.example.com/api/quote",
                "quote_method": "POST",
                "quote_fields": {"postcode": "{postcode}", "litres": "{quantity_liters}"},
                "price_regex": r'"price_per_liter"\s*:\s*(\d+\.\d+)',
            },
        }

    def test_quote_via_form(self) -> None:
        with patch("oilwatch.connectors.http_form.httpx.Client") as Client:
            Client.return_value.request.return_value = fake_response('{"price_per_liter": 1.42}')
            result = HTTPFormConnector().quote(self.supplier, 1000, {"postcode": "AB21 0YA"})
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.price_per_liter, 1.42)
        self.assertEqual(result.total_price, 1420.0)

    def test_place_order_without_order_url(self) -> None:
        result = HTTPFormConnector().place_order(self.supplier, 1000, 1.42, {})
        self.assertEqual(result.status, "manual_action_required")


if __name__ == "__main__":
    unittest.main()
