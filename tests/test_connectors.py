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

    def test_a_supplier_with_a_quote_page_is_not_reported_as_having_none(self) -> None:
        """The reason has to say which way the supplier is out of reach.

        A quote page that answers a person is not the same as no quote page, and
        the two were conflated: five suppliers with a working quote form
        (Compass Fuels, Crown Oil, Gleaner Oils, Nationwide Fuels, Oilfast Insch)
        were all reported as ``no_quote_page``, which tells a reader there is
        nowhere to go - the opposite of the truth.
        """
        supplier = {
            "id": 2,
            "name": "B",
            "phone": "01224 123456",
            "website": "https://b.example.com",
            "connector_config": {"order_page": "https://b.example.com/get-a-quote/"},
        }
        result = ManualConnector().quote(supplier, 1000, {})
        self.assertEqual(result.reason, "quote_by_request")
        self.assertIn("https://b.example.com/get-a-quote/", result.notes)

    def test_a_supplier_with_no_quote_page_still_says_so(self) -> None:
        supplier = {"id": 3, "name": "C", "phone": "01224 123456", "website": "https://c.example.com"}
        result = ManualConnector().quote(supplier, 1000, {})
        self.assertEqual(result.reason, "no_quote_page")
        # No order page to name, so the note must not imply one.
        self.assertNotIn("Quote page:", result.notes)


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
        # Patch where httpx is actually used: price_page builds its client
        # through oilwatch.http, so patching a name on the connector mocks
        # nothing.
        with patch("oilwatch.http.httpx.Client") as Client:
            Client.return_value.get.return_value = fake_response("Kerosene 155.80p/L delivered today")
            result = PricePageConnector().quote(self.supplier, 1000, {})
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.price_per_liter, 1.558)
        self.assertEqual(result.total_price, 1558.0)

    def test_quote_applies_vat_when_configured(self) -> None:
        self.supplier["connector_config"]["vat_rate"] = 0.05
        with patch("oilwatch.http.httpx.Client") as Client:
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

    def _supplier(self, **config) -> dict:
        return {**self.supplier, "connector_config": {**self.supplier["connector_config"], **config}}

    def test_quote_via_form(self) -> None:
        with patch("oilwatch.connectors.http_form.httpx.Client") as Client:
            Client.return_value.request.return_value = fake_response('{"price_per_liter": 1.42}')
            result = HTTPFormConnector().quote(self.supplier, 1000, {"postcode": "AB21 0YA"})
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.price_per_liter, 1.42)
        self.assertEqual(result.total_price, 1420.0)

    def test_quote_raises_when_no_price_matches(self) -> None:
        """A redesigned response must fail loudly, not be read as a price."""
        with patch("oilwatch.connectors.http_form.httpx.Client") as Client:
            Client.return_value.request.return_value = fake_response("<html>No prices today</html>")
            with self.assertRaises(ValueError) as caught:
                HTTPFormConnector().quote(self.supplier, 1000, {"postcode": "AB21 0YA"})

        self.assertIn("No price matched", str(caught.exception))

    def test_quote_raises_when_the_matched_text_is_not_a_price(self) -> None:
        supplier = self._supplier(price_regex=r'"price_per_liter"\s*:\s*([^,}]+)')
        with patch("oilwatch.connectors.http_form.httpx.Client") as Client:
            Client.return_value.request.return_value = fake_response('{"price_per_liter": "POA"}')
            with self.assertRaises(ValueError) as caught:
                HTTPFormConnector().quote(supplier, 1000, {})

        self.assertIn("Could not parse price", str(caught.exception))

    def test_quote_applies_a_configured_ex_vat_rate(self) -> None:
        supplier = self._supplier(vat_rate=0.05)
        with patch("oilwatch.connectors.http_form.httpx.Client") as Client:
            Client.return_value.request.return_value = fake_response('{"price_per_liter": 1.42}')
            result = HTTPFormConnector().quote(supplier, 1000, {"postcode": "AB21 0YA"})

        price_per_liter = result.price_per_liter
        assert price_per_liter is not None
        self.assertAlmostEqual(price_per_liter, 1.491, places=4)

    def test_fields_render_the_templates_and_leave_the_rest_alone(self) -> None:
        """quote_fields holds templates and literal values; only strings are formatted."""
        supplier = self._supplier(
            quote_fields={"postcode": "{postcode}", "litres": "{quantity_liters}", "fixed": 1000}
        )
        with patch("oilwatch.connectors.http_form.httpx.Client") as Client:
            Client.return_value.request.return_value = fake_response('{"price_per_liter": 1.42}')
            HTTPFormConnector().quote(supplier, 1000, {"postcode": "AB21 0YA"})

        data = Client.return_value.request.call_args.kwargs["data"]
        self.assertEqual(data, {"postcode": "AB21 0YA", "litres": "1000", "fixed": 1000})

if __name__ == "__main__":
    unittest.main()
