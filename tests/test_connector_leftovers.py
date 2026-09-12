"""The remaining connector branches: Highland Fuels' request flow and
BoilerJuice's login, dropdown and element-price paths.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

import httpx

from oilwatch.connectors.suppliers.boilerjuice import BoilerJuiceBrowserConnector
from oilwatch.connectors.suppliers.highland_fuels import HighlandFuelsConnector
from oilwatch.pricing import DOMESTIC_VAT_RATE, apply_vat
from tests.fake_async_page import FakeAsyncPage, FakeElement

SUPPLIER = {"id": 6, "name": "Highland Fuels", "website": "https://www.highlandfuels.co.uk", "phone": "0800"}

OFFERS_XML = (
    "<Offers>"
    "<Offer><UnitPrice>104.50</UnitPrice></Offer>"
    "<Offer><UnitPrice>101.03</UnitPrice></Offer>"
    "</Offers>"
)


class FakeHttpResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class HighlandFuelsTests(unittest.TestCase):
    def _quote(self, *, response=None, error=None, supplier=None):
        target = "oilwatch.connectors.suppliers.highland_fuels.request_with_retry"
        with patch(target, side_effect=error, return_value=response) as request:
            result = HighlandFuelsConnector().quote(supplier or SUPPLIER, 1000, {"postcode": "AB21 0YA"})
        return result, request

    def test_an_offer_is_read_and_vat_applied(self) -> None:
        result, request = self._quote(response=FakeHttpResponse(OFFERS_XML))

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "highland_fuels")
        self.assertAlmostEqual(result.price_per_liter, apply_vat(1.0103, DOMESTIC_VAT_RATE), places=4)
        body = request.call_args.kwargs["content"]
        self.assertIn("<Product>043</Product>", body)
        self.assertIn("<PostCode>AB21 0YA</PostCode>", body)

    def test_a_configured_product_is_used(self) -> None:
        supplier = {**SUPPLIER, "connector_config": {"product_value": "999"}}
        _, request = self._quote(response=FakeHttpResponse(OFFERS_XML), supplier=supplier)
        self.assertIn("<Product>999</Product>", request.call_args.kwargs["content"])

    def test_a_transport_error_needs_a_manual_quote(self) -> None:
        result, _ = self._quote(error=httpx.ConnectError("no route"))
        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("HTTP error", result.notes)

    def test_a_response_without_an_offer_needs_a_manual_quote(self) -> None:
        result, _ = self._quote(response=FakeHttpResponse("<Offers></Offers>"))
        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("No offer/price", result.notes)


class BoilerJuiceLoginTests(unittest.TestCase):
    def test_credentials_are_entered_and_the_form_submitted(self) -> None:
        email, password, button = FakeElement(), FakeElement(), FakeElement(tag="BUTTON")
        page = FakeAsyncPage(elements=[("email", email), ("password", password), ("Login", button)])

        # The fake shows no signed-in marker, so the run ends in the no-marker
        # failure: this pins both that the fill/submit happened and that the
        # failure now carries a reason rather than the old silent False.
        with self.assertRaises(RuntimeError) as raised:
            asyncio.run(BoilerJuiceBrowserConnector().login(page, "owner@example.test", "pw"))

        self.assertEqual(email.filled, ["owner@example.test"])
        self.assertEqual(password.filled, ["pw"])
        self.assertEqual(button.clicked, 1)
        self.assertIn("signed-in", str(raised.exception))


class BoilerJuiceQuoteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = BoilerJuiceBrowserConnector()

    def _quote(self, page):
        return asyncio.run(
            self.connector.get_quote_with_browser(SUPPLIER, 1000, {"postcode": "AB21 0YA"}, page)
        )

    def test_a_quantity_dropdown_is_set_by_option_value(self) -> None:
        option = FakeElement(attributes={"value": "1000"})
        select = FakeElement(tag="SELECT", options=[option])
        page = FakeAsyncPage(
            content="Price: £0.85 per litre",
            elements=[("postcode", FakeElement()), ("quantity", select)],
        )
        result = self._quote(page)

        self.assertEqual(result.status, "ok")
        self.assertEqual(select.selected, ["1000"])

    def test_the_price_is_read_from_a_price_element_when_the_text_has_none(self) -> None:
        price_el = FakeElement(text="£1.08 per litre")
        page = FakeAsyncPage(
            content="<html>Live prices</html>",
            selector_all=[(".price", [price_el])],
        )
        result = self._quote(page)

        self.assertEqual(result.status, "ok")
        self.assertAlmostEqual(result.price_per_liter, 1.08, places=4)


if __name__ == "__main__":
    unittest.main()
