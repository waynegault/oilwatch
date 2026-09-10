"""The shared connector base classes.

Their behaviour is exercised through the concrete connectors; these pin the
contracts directly, so a change to a base is caught here rather than only through
a supplier connector's expectations.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import patch

from oilwatch.connectors.browser_base import BrowserConnector
from oilwatch.connectors.sync_browser import SyncBrowserConnector, sync_page
from oilwatch.models import QuoteResult
from tests.fake_async_page import FakeAsyncPage, FakeElement

SUPPLIER = {"id": 1, "name": "Test Supplier", "website": "https://example.test"}


class StubSyncConnector(SyncBrowserConnector):
    source = "stub"
    price_description = "Stub page"
    no_price_note = "No price on the stub page."
    order_notes = "Order via the stub."

    def __init__(self, price: float | None) -> None:
        self._price = price

    def collect_price(self, page: Any, supplier: dict, quantity_liters: int, context: dict):
        return self._price, {"quote_url": "https://example.test"}


class SyncBrowserConnectorTests(unittest.TestCase):
    def test_quote_shapes_an_ok_result(self) -> None:
        result = StubSyncConnector(1.10).quote(SUPPLIER, 1000, {"postcode": "AB21 0YA"})

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "stub")
        self.assertAlmostEqual(result.price_per_liter, 1.155, places=4)  # 1.10 + 5% VAT
        self.assertAlmostEqual(result.total_price, 1155.0, places=2)
        self.assertIn("Stub page", result.notes)
        self.assertIn("AB21 0YA", result.notes)

    def test_no_price_falls_back_to_manual(self) -> None:
        result = StubSyncConnector(None).quote(SUPPLIER, 1000, {})
        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("No price on the stub page.", result.notes)

    def test_a_collect_error_becomes_a_manual_quote(self) -> None:
        class Boom(StubSyncConnector):
            def collect_price(self, *args: Any, **kwargs: Any):
                raise RuntimeError("boom")

        result = Boom(None).quote(SUPPLIER, 1000, {})
        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("Browser automation error: boom", result.notes)

    def test_place_order_uses_the_subclass_note(self) -> None:
        order = StubSyncConnector(1.0).place_order(SUPPLIER, 1000, 1.05, {})
        self.assertEqual(order.status, "manual_action_required")
        self.assertEqual(order.notes, "Order via the stub.")

    def test_sync_page_yields_a_page_and_closes_the_browser(self) -> None:
        closed: list[bool] = []

        class FakeBrowser:
            def new_page(self):
                return "PAGE"

            def close(self) -> None:
                closed.append(True)

        class FakeChromium:
            def launch(self, **kwargs: Any) -> FakeBrowser:
                return FakeBrowser()

        class FakePlaywright:
            def __init__(self) -> None:
                self.chromium = FakeChromium()

        class FakeCM:
            def __enter__(self) -> FakePlaywright:
                return FakePlaywright()

            def __exit__(self, *exc: Any) -> bool:
                return False

        with patch("playwright.sync_api.sync_playwright", side_effect=lambda: FakeCM()):
            with sync_page() as page:
                self.assertEqual(page, "PAGE")

        self.assertTrue(closed)


class StubBrowserConnector(BrowserConnector):
    def __init__(self) -> None:
        super().__init__()
        self.supplier_key = "stub"
        self.supplier_name = "Stub Fuels"
        self.base_url = "https://example.test"
        self.login_url = "https://example.test/login"

    async def login(self, page: Any, email: str, password: str) -> bool:
        return True

    async def get_quote_with_browser(self, supplier: dict, quantity_liters: int, context: dict, page: Any):
        return QuoteResult(
            supplier_id=1,
            supplier_name="Test Supplier",
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="ok",
            source="stub",
        )


class BrowserConnectorBaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = StubBrowserConnector()
        self.creds = {"email": "owner@example.test", "password": "Secret!123"}

    def test_place_order_is_manual_and_names_the_site(self) -> None:
        order = self.connector.place_order(SUPPLIER, 1000, 1.05, {})
        self.assertEqual(order.status, "manual_action_required")
        self.assertIn(self.connector.base_url, order.notes)

    def test_registration_is_offered_when_a_register_link_exists(self) -> None:
        page = FakeAsyncPage(elements=[("Register", FakeElement())])
        result = asyncio.run(
            self.connector._handle_registration_or_error(SUPPLIER, 1000, {}, page, self.creds)
        )

        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("owner@example.test", result.notes)
        self.assertIn("Secret!123", result.notes)

    def test_no_register_link_becomes_an_error(self) -> None:
        result = asyncio.run(
            self.connector._handle_registration_or_error(
                SUPPLIER, 1000, {}, FakeAsyncPage(), self.creds, "login failed"
            )
        )

        self.assertEqual(result.status, "error")
        self.assertIn("login failed", result.notes)


if __name__ == "__main__":
    unittest.main()
