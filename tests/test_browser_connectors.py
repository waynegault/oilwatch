"""Behavioural tests for the async Playwright supplier connectors.

Driven against ``tests.fake_async_page`` rather than a real browser: these cover
the supplier-specific login/quote logic, which is where the bugs live. The HTTP
fallback is patched out so a test can never reach the network.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from oilwatch.connectors.suppliers.boilerjuice import BoilerJuiceBrowserConnector
from oilwatch.connectors.suppliers.homefuels_direct_browser import HomeFuelsDirectBrowserConnector
from oilwatch.connectors.suppliers.valueoils_browser import ValueOilsBrowserConnector
from tests.fake_async_page import FakeAsyncPage, FakeElement

SUPPLIER = {
    "id": 5,
    "name": "Test Supplier",
    "website": "https://example.test",
    "phone": "0800 000000",
}


def _quote(connector, page, quantity: int = 1000):
    return asyncio.run(
        connector.get_quote_with_browser(SUPPLIER, quantity, {"postcode": "AB21 0YA"}, page)
    )


class BoilerJuiceConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = BoilerJuiceBrowserConnector()

    def test_extracts_price_and_applies_vat(self) -> None:
        page = FakeAsyncPage(
            content="Heating oil today: £0.85 per litre",
            elements=[
                ("postcode", FakeElement()),
                ("quantity", FakeElement(tag="INPUT")),
                ('has-text("Quote")', FakeElement(tag="BUTTON")),
            ],
        )
        result = _quote(self.connector, page)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "boilerjuice_browser")
        self.assertAlmostEqual(result.price_per_liter, 0.85, places=4)
        # 0.85 * 1000 = £850 ex VAT, +5% VAT = £892.50.
        self.assertAlmostEqual(result.total_price, 892.5, places=2)

    def test_no_price_is_manual_not_fabricated(self) -> None:
        result = _quote(self.connector, FakeAsyncPage(content="<html>no price here</html>"))
        self.assertEqual(result.status, "manual_action_required")
        self.assertIsNone(result.price_per_liter)

    def test_login_reports_already_logged_in(self) -> None:
        page = FakeAsyncPage(elements=[("logout", FakeElement())])
        self.assertTrue(asyncio.run(self.connector.login(page, "owner@example.test", "pw")))

    def test_login_returns_false_without_a_form(self) -> None:
        self.assertFalse(asyncio.run(self.connector.login(FakeAsyncPage(), "owner@example.test", "pw")))

    def test_place_order_is_manual(self) -> None:
        # Regression: this built an OrderResult the module never imported, so it
        # raised NameError instead of returning a manual order.
        order = self.connector.place_order(SUPPLIER, 1000, 1.05, {})
        self.assertEqual(order.status, "manual_action_required")
        self.assertIn(self.connector.quote_url, order.notes)


class ValueOilsBrowserConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = ValueOilsBrowserConnector()

    def test_fills_the_form_and_applies_5pc_vat(self) -> None:
        usage, fuel = FakeElement(tag="SELECT"), FakeElement(tag="SELECT")
        postcode, quantity, button = FakeElement(), FakeElement(), FakeElement(tag="BUTTON")
        page = FakeAsyncPage(
            content="Heating Oil Kerosene 103.90p per litre",
            elements=[
                ("usage_type", usage),
                ("fuel_type", fuel),
                ("postcode", postcode),
                ("quantity", quantity),
                ('has-text("Quote")', button),
            ],
        )
        result = _quote(self.connector, page)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "valueoils_browser")
        self.assertAlmostEqual(result.price_per_liter, 1.039, places=4)
        self.assertAlmostEqual(result.total_price, 1090.95, places=2)
        self.assertEqual(usage.selected, ["domestic"])
        self.assertEqual(fuel.selected, ["kerosene"])
        self.assertEqual(postcode.filled, ["AB21 0YA"])
        self.assertEqual(quantity.filled, ["1000"])
        self.assertEqual(button.clicked, 1)

    def test_falls_back_to_http_when_no_price_is_shown(self) -> None:
        sentinel = object()
        with patch.object(
            ValueOilsBrowserConnector, "_fallback_to_http", new=AsyncMock(return_value=sentinel)
        ) as fallback:
            result = _quote(self.connector, FakeAsyncPage(content="<html>no price</html>"))
        fallback.assert_awaited_once()
        self.assertIs(result, sentinel)


class HomeFuelsDirectBrowserConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = HomeFuelsDirectBrowserConnector()

    def test_extracts_pence_per_litre_and_applies_20pc_vat(self) -> None:
        postcode, quantity, button = FakeElement(), FakeElement(), FakeElement(tag="BUTTON")
        page = FakeAsyncPage(
            content="Our price today: 138 pence per litre",
            elements=[("postcode", postcode), ("quantity", quantity), ('has-text("Price")', button)],
        )
        result = _quote(self.connector, page)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "homefuels_direct_browser")
        self.assertAlmostEqual(result.price_per_liter, 1.38, places=4)
        self.assertAlmostEqual(result.total_price, 1656.0, places=2)
        self.assertEqual(postcode.filled, ["AB21 0YA"])

    def test_falls_back_to_http_when_no_price_is_shown(self) -> None:
        sentinel = object()
        with patch.object(
            HomeFuelsDirectBrowserConnector, "_fallback_to_http", new=AsyncMock(return_value=sentinel)
        ) as fallback:
            result = _quote(self.connector, FakeAsyncPage(content="<html>no price</html>"))
        fallback.assert_awaited_once()
        self.assertIs(result, sentinel)


if __name__ == "__main__":
    unittest.main()
