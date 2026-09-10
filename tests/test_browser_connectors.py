"""Behavioural tests for the async Playwright supplier connectors.

Driven against ``tests.fake_async_page`` rather than a real browser: these cover
the supplier-specific login/quote logic, which is where the bugs live.
"""

from __future__ import annotations

import asyncio
import unittest

from oilwatch.connectors.suppliers.boilerjuice import BoilerJuiceBrowserConnector
from tests.fake_async_page import FakeAsyncPage, FakeElement

SUPPLIER = {
    "id": 5,
    "name": "BoilerJuice",
    "website": "https://www.boilerjuice.com",
    "phone": "0800 000000",
}


class BoilerJuiceConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = BoilerJuiceBrowserConnector()

    def _quote(self, page: FakeAsyncPage):
        return asyncio.run(
            self.connector.get_quote_with_browser(SUPPLIER, 1000, {"postcode": "AB21 0YA"}, page)
        )

    def test_extracts_price_and_applies_vat(self) -> None:
        page = FakeAsyncPage(
            content="Heating oil today: £0.85 per litre",
            elements=[
                ("postcode", FakeElement()),
                ("quantity", FakeElement(tag="INPUT")),
                ('has-text("Quote")', FakeElement(tag="BUTTON")),
            ],
        )
        result = self._quote(page)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "boilerjuice_browser")
        self.assertAlmostEqual(result.price_per_liter, 0.85, places=4)
        # 0.85 * 1000 = £850 ex VAT, +5% VAT = £892.50.
        self.assertAlmostEqual(result.total_price, 892.5, places=2)

    def test_no_price_is_manual_not_fabricated(self) -> None:
        result = self._quote(FakeAsyncPage(content="<html>no price here</html>"))
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


if __name__ == "__main__":
    unittest.main()
