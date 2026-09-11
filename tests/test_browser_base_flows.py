"""BrowserConnector's orchestration, with the browser faked.

_setup_browser, _close_browser, the request interception and discover_api run
against a fake async_playwright; quote()'s branches are driven with the
connector's own hooks stubbed.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from oilwatch.connectors.browser_base import BrowserConnector
from oilwatch.identity import Contact
from oilwatch.models import QuoteResult
from tests.fake_async_page import FakeAsyncPage

SUPPLIER = {"id": 1, "name": "Test Supplier", "website": "https://example.test"}


class CredentialFallbackTests(unittest.TestCase):
    """A supplier with no stored account gets one generated rather than skipped.

    The orchestration tests stub this method out, so its body had never run: it
    is what turns "no credentials" into a password the owner can use.
    """

    def setUp(self) -> None:
        self.connector = StubConnector()

    def test_stored_credentials_are_used_as_they_are(self) -> None:
        stored = {"email": "owner@example.test", "password": "hunter2"}
        with patch(
            "oilwatch.connectors.browser_base.get_supplier_credentials", return_value=stored
        ) as get_credentials:
            self.assertIs(self.connector._get_or_create_credentials(SUPPLIER), stored)

        get_credentials.assert_called_once_with("stub")

    def test_missing_credentials_are_generated_and_stored(self) -> None:
        with (
            patch("oilwatch.connectors.browser_base.get_supplier_credentials", return_value=None),
            patch("oilwatch.connectors.browser_base.store_supplier_credentials") as store,
            patch(
                "oilwatch.connectors.browser_base.load_contact",
                return_value=Contact(email="owner@example.test"),
            ),
        ):
            creds = self.connector._get_or_create_credentials(SUPPLIER)

        self.assertEqual(creds["email"], "owner@example.test")
        self.assertTrue(creds["password"])
        # The generated password is what gets stored, so a manual sign-in works.
        self.assertEqual(store.call_args.kwargs["password"], creds["password"])
        self.assertEqual(store.call_args.kwargs["supplier_key"], "stub")
        self.assertEqual(store.call_args.kwargs["supplier_name"], "Stub Fuels")


class FakeRequest:
    def __init__(self, url: str = "https://example.test/api/quote") -> None:
        self.url = url
        self.method = "GET"
        self.headers: dict[str, str] = {}
        self.post_data = None


class FakeResponse:
    def __init__(self, text: str = '{"PPL": 1.05}', content_type: str = "application/json") -> None:
        self._text = text
        self.status = 200
        self.headers = {"content-type": content_type}

    async def text(self) -> str:
        return self._text


class FakeRoute:
    def __init__(self, request: FakeRequest) -> None:
        self.request = request
        self._response = FakeResponse()
        self.fulfilled = False

    async def fetch(self) -> FakeResponse:
        return self._response

    async def fulfill(self, response: Any = None) -> None:
        self.fulfilled = True


class FakeContext:
    def __init__(self, page: Any) -> None:
        self._page = page
        self.route_handler = None
        self.closed = False

    async def route(self, pattern: str, handler) -> None:
        self.route_handler = handler

    async def new_page(self) -> Any:
        return self._page

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self._context = context
        self.closed = False

    async def new_context(self, **kwargs: Any) -> FakeContext:
        return self._context

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, browser: FakeBrowser) -> None:
        self._browser = browser

    async def launch(self, **kwargs: Any) -> FakeBrowser:
        return self._browser


class FakeAsyncPlaywright:
    def __init__(self, browser: FakeBrowser) -> None:
        self.chromium = FakeChromium(browser)
        self.stopped = False

    async def start(self) -> "FakeAsyncPlaywright":
        return self

    async def stop(self) -> None:
        self.stopped = True


class StubConnector(BrowserConnector):
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


def _wired():
    page = FakeAsyncPage()
    context = FakeContext(page)
    browser = FakeBrowser(context)
    return StubConnector(), page, context, FakeAsyncPlaywright(browser)


def _fake_playwright(playwright):
    return patch("oilwatch.connectors.browser_base.async_playwright", return_value=playwright)


class SetupAndInterceptionTests(unittest.TestCase):
    def test_setup_wires_the_context_and_captures_the_page(self) -> None:
        connector, page, context, playwright = _wired()
        with _fake_playwright(playwright):
            returned = asyncio.run(connector._setup_browser(headless=True))

        self.assertIs(returned, page)
        self.assertIs(connector._page, page)
        self.assertIsNotNone(context.route_handler)

    def test_close_releases_the_context_and_playwright(self) -> None:
        connector, _, context, playwright = _wired()
        with _fake_playwright(playwright):
            asyncio.run(connector._setup_browser())
            asyncio.run(connector._close_browser())

        self.assertTrue(context.closed)
        self.assertTrue(playwright.stopped)
        self.assertIsNone(connector._page)

    def test_interception_records_api_traffic(self) -> None:
        connector, _, context, playwright = _wired()
        with _fake_playwright(playwright):
            asyncio.run(connector._setup_browser())

        route = FakeRoute(FakeRequest())
        asyncio.run(context.route_handler(route))

        self.assertTrue(route.fulfilled)
        self.assertEqual(len(connector._api_requests), 1)
        self.assertEqual(connector._api_responses[0]["url"], route.request.url)

    def test_discover_api_reports_what_it_saw(self) -> None:
        connector, page, _, playwright = _wired()
        with _fake_playwright(playwright):
            seen = asyncio.run(connector.discover_api())

        self.assertEqual(seen["base_url"], connector.base_url)
        self.assertIn(connector.base_url, page.goto_urls)


class QuoteOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector, self.page, _, _ = _wired()
        self.creds = {"email": "owner@example.test", "password": "Secret!123"}

    def _base_patches(self):
        return (
            patch.object(self.connector, "_setup_browser", new=AsyncMock(return_value=self.page)),
            patch.object(self.connector, "_close_browser", new=AsyncMock()),
            patch.object(self.connector, "_get_or_create_credentials", return_value=self.creds),
        )

    def test_a_successful_login_returns_the_quote(self) -> None:
        setup, close, creds = self._base_patches()
        with setup, close, creds:
            result = self.connector.quote(SUPPLIER, 1000, {})

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "stub")

    def test_a_refused_login_becomes_a_manual_or_error_result(self) -> None:
        with (
            patch.object(self.connector, "_setup_browser", new=AsyncMock(return_value=self.page)),
            patch.object(self.connector, "_close_browser", new=AsyncMock()),
            patch.object(self.connector, "_get_or_create_credentials", return_value=self.creds),
            patch.object(self.connector, "login", new=AsyncMock(return_value=False)),
        ):
            result = self.connector.quote(SUPPLIER, 1000, {})

        self.assertEqual(result.status, "error")
        self.assertIn("Login failed", result.notes)

    def test_a_login_exception_is_caught_and_reported(self) -> None:
        with (
            patch.object(self.connector, "_setup_browser", new=AsyncMock(return_value=self.page)),
            patch.object(self.connector, "_close_browser", new=AsyncMock()),
            patch.object(self.connector, "_get_or_create_credentials", return_value=self.creds),
            patch.object(self.connector, "login", new=AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            result = self.connector.quote(SUPPLIER, 1000, {})

        self.assertEqual(result.status, "error")
        self.assertIn("boom", result.notes)

    def test_a_quote_failure_is_reported_as_a_browser_error(self) -> None:
        with (
            patch.object(self.connector, "_setup_browser", new=AsyncMock(return_value=self.page)),
            patch.object(self.connector, "_close_browser", new=AsyncMock()),
            patch.object(self.connector, "_get_or_create_credentials", return_value=self.creds),
            patch.object(
                self.connector, "get_quote_with_browser", new=AsyncMock(side_effect=RuntimeError("no form"))
            ),
        ):
            result = self.connector.quote(SUPPLIER, 1000, {})

        self.assertEqual(result.status, "error")
        self.assertIn("Browser automation error: no form", result.notes)


if __name__ == "__main__":
    unittest.main()
