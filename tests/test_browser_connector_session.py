"""The browser connectors' session handling and their HTTP fallback.

login() is driven through the fake page; _fallback_to_http runs against a faked
httpx async client, so neither touches the network.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from oilwatch.connectors.suppliers.homefuels_direct_browser import HomeFuelsDirectBrowserConnector
from oilwatch.connectors.suppliers.valueoils_browser import ValueOilsBrowserConnector
from tests.fake_async_page import FakeAsyncPage, FakeElement

SUPPLIER = {"id": 4, "name": "Test Supplier", "website": "https://example.test", "phone": "0800"}

CONNECTORS = [ValueOilsBrowserConnector, HomeFuelsDirectBrowserConnector]


class BoomPage(FakeAsyncPage):
    async def goto(self, url: str, **kwargs) -> None:
        raise RuntimeError("navigation failed")


class FakeHttpResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class FakeAsyncClient:
    def __init__(self, text: str = "", get_raises: bool = False) -> None:
        self._text = text
        self._get_raises = get_raises
        self.urls: list[str] = []

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def get(self, url: str) -> FakeHttpResponse:
        self.urls.append(url)
        if self._get_raises:
            raise RuntimeError("connection reset")
        return FakeHttpResponse(self._text)


class LoginTests(unittest.TestCase):
    def test_an_existing_session_is_accepted(self) -> None:
        for connector_cls in CONNECTORS:
            with self.subTest(connector=connector_cls.__name__):
                page = FakeAsyncPage(elements=[("logout", FakeElement())])
                self.assertTrue(asyncio.run(connector_cls().login(page, "a@b.c", "pw")))

    def test_login_is_not_required_when_there_is_no_form(self) -> None:
        for connector_cls in CONNECTORS:
            with self.subTest(connector=connector_cls.__name__):
                self.assertTrue(asyncio.run(connector_cls().login(FakeAsyncPage(), "a@b.c", "pw")))

    def test_a_navigation_failure_still_allows_quoting(self) -> None:
        for connector_cls in CONNECTORS:
            with self.subTest(connector=connector_cls.__name__):
                self.assertTrue(asyncio.run(connector_cls().login(BoomPage(), "a@b.c", "pw")))


class FallbackTests(unittest.TestCase):
    def _fallback(self, connector, client):
        with patch("httpx.AsyncClient", return_value=client):
            return asyncio.run(connector._fallback_to_http(SUPPLIER, 1000, {"postcode": "AB21 0YA"}))

    def test_valueoils_reads_the_price_from_the_fallback_page(self) -> None:
        client = FakeAsyncClient("Heating Oil 900 Litres 103.90p")
        result = self._fallback(ValueOilsBrowserConnector(), client)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "valueoils_http_fallback")
        self.assertAlmostEqual(result.price_per_liter, 1.039, places=4)
        self.assertAlmostEqual(result.total_price, 1090.95, places=2)
        self.assertEqual(client.urls, [ValueOilsBrowserConnector().quote_url])

    def test_valueoils_fallback_without_a_price_is_an_error(self) -> None:
        result = self._fallback(ValueOilsBrowserConnector(), FakeAsyncClient("<html>nothing</html>"))
        self.assertEqual(result.status, "error")
        self.assertIn("Could not extract price", result.notes)

    def test_valueoils_fallback_reports_a_failed_fetch(self) -> None:
        result = self._fallback(ValueOilsBrowserConnector(), FakeAsyncClient(get_raises=True))
        self.assertEqual(result.status, "error")
        self.assertIn("HTTP fallback failed", result.notes)

    def test_homefuels_reads_the_pence_average_from_the_fallback_page(self) -> None:
        result = self._fallback(HomeFuelsDirectBrowserConnector(), FakeAsyncClient("UK Average 138 pence"))

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "homefuels_direct_http_fallback")
        self.assertAlmostEqual(result.price_per_liter, 1.38, places=4)
        self.assertAlmostEqual(result.total_price, 1656.0, places=2)

    def test_homefuels_fallback_without_a_price_needs_manual_action(self) -> None:
        result = self._fallback(HomeFuelsDirectBrowserConnector(), FakeAsyncClient("<html>nothing</html>"))
        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("Could not extract automated price", result.notes)

    def test_homefuels_fallback_reports_a_failed_fetch(self) -> None:
        result = self._fallback(HomeFuelsDirectBrowserConnector(), FakeAsyncClient(get_raises=True))
        self.assertEqual(result.status, "error")
        self.assertIn("HTTP fallback failed", result.notes)


if __name__ == "__main__":
    unittest.main()
