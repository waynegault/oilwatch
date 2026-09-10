"""Characterization tests for the synchronous Playwright connectors.

These drive Fuelsoft and Rix against a fake Playwright so the page-interaction
sequence and the result mapping (ok vs manual_action_required) are pinned. They
exist so the shared sync-browser base can be refactored without silently
changing supplier behaviour, which has no other offline coverage.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from oilwatch.connectors.suppliers.fuelsoft import FuelsoftConnector
from oilwatch.connectors.suppliers.rix_browser import RixBrowserConnector
from oilwatch.pricing import DOMESTIC_VAT_RATE, apply_vat

SUPPLIER = {
    "id": 7,
    "name": "Test Supplier",
    "website": "https://example.test",
    "phone": "01234 567890",
    "email": "owner@example.test",
}


class FakeResponse:
    def __init__(self, url: str, body: object) -> None:
        self.url = url
        self._body = body

    def json(self) -> object:
        return self._body

    def text(self) -> str:
        return str(self._body)


class FakeKeyboard:
    def press(self, key: str) -> None:
        pass


class FakePage:
    """Minimal stand-in for a Playwright sync page."""

    def __init__(
        self,
        *,
        text: str = "",
        url: str = "https://example.test/quote",
        fail_goto: bool = False,
        on_wait_response: tuple[str, object] | None = None,
    ) -> None:
        self.text = text
        self.url = url
        self.fail_goto = fail_goto
        self.calls: list[tuple] = []
        self.handlers: dict[str, object] = {}
        self.keyboard = FakeKeyboard()
        self._on_wait_response = on_wait_response

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler

    def goto(self, url: str, **kwargs) -> None:
        self.calls.append(("goto", url))
        if self.fail_goto:
            raise RuntimeError("navigation failed")

    def wait_for_timeout(self, ms: int) -> None:
        self.calls.append(("wait_for_timeout", ms))
        if self._on_wait_response and "response" in self.handlers:
            url, body = self._on_wait_response
            self._on_wait_response = None
            self.handlers["response"](FakeResponse(url, body))

    def wait_for_function(self, script: str, timeout: int | None = None) -> None:
        self.calls.append(("wait_for_function",))

    def query_selector(self, selector: str):
        return None

    def click(self, selector: str, **kwargs) -> None:
        self.calls.append(("click", selector))

    def fill(self, selector: str, value: str, **kwargs) -> None:
        self.calls.append(("fill", selector, value))

    def select_option(self, selector: str, **kwargs) -> None:
        self.calls.append(("select_option", selector))

    def evaluate(self, script: str) -> None:
        self.calls.append(("evaluate", script))

    def eval_on_selector(self, selector: str, script: str) -> str:
        self.calls.append(("eval_on_selector", selector))
        return self.text


class FakeBrowser:
    def __init__(self, page: FakePage) -> None:
        self._page = page
        self.closed = False

    def new_page(self) -> FakePage:
        return self._page

    def close(self) -> None:
        self.closed = True


class FakePlaywright:
    def __init__(self, page: FakePage) -> None:
        self._page = page
        self.stopped = False

    class _Chromium:
        def __init__(self, page: FakePage) -> None:
            self._page = page

        def launch(self, **kwargs) -> FakeBrowser:
            return FakeBrowser(self._page)

    @property
    def chromium(self) -> "FakePlaywright._Chromium":
        return self._Chromium(self._page)

    def stop(self) -> None:
        self.stopped = True


class FakeSyncPlaywrightCM:
    def __init__(self, page: FakePage) -> None:
        self._pw = FakePlaywright(page)

    def __enter__(self) -> FakePlaywright:
        return self._pw

    def __exit__(self, *exc_info) -> bool:
        self._pw.stop()
        return False


def _patcher(page: FakePage):
    return patch("playwright.sync_api.sync_playwright", side_effect=lambda: FakeSyncPlaywrightCM(page))


class FuelsoftConnectorTests(unittest.TestCase):
    QUOTE_URL = "https://oilweb.example/OnlineQuote.aspx"

    def _supplier(self) -> dict:
        return {**SUPPLIER, "connector_config": {"quote_url": self.QUOTE_URL}}

    def test_quote_reads_ppl_from_the_quote_api(self) -> None:
        page = FakePage(
            on_wait_response=(
                "https://oilweb.example/JOil/fuelsoftapi/Quotes/deliveryschedules/quote/1",
                [{"PPL": 1.0878}, {"PPL": 1.1021}],
            )
        )
        with _patcher(page):
            result = FuelsoftConnector().quote(self._supplier(), 1000, {"postcode": "AB21 0YA"})

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "fuelsoft")
        self.assertEqual(result.raw_payload["price_ex_vat"], 1.0878)
        self.assertAlmostEqual(
            result.price_per_liter, apply_vat(1.0878, DOMESTIC_VAT_RATE), places=4
        )
        self.assertIn(("goto", self.QUOTE_URL), page.calls)

    def test_navigation_failure_falls_back_to_manual(self) -> None:
        page = FakePage(fail_goto=True)
        with _patcher(page):
            result = FuelsoftConnector().quote(self._supplier(), 1000, {})
        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("Browser automation error", result.notes)

    def test_response_without_a_price_falls_back_to_manual(self) -> None:
        page = FakePage(
            on_wait_response=(
                "https://oilweb.example/JOil/fuelsoftapi/Quotes/deliveryschedules/quote/1",
                [{"Goods": 1087.8}],
            )
        )
        with _patcher(page):
            result = FuelsoftConnector().quote(self._supplier(), 1000, {})
        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("Could not extract a price from the quote response.", result.notes)


class RixBrowserConnectorTests(unittest.TestCase):
    def test_quote_reads_ppl_from_the_results_page(self) -> None:
        page = FakePage(
            text="Delivery option\nPPL (ex. VAT) 110.35p\n",
            url="https://fuelquote.rix.co.uk/your-quote/123",
        )
        with _patcher(page):
            result = RixBrowserConnector().quote(SUPPLIER, 1000, {"postcode": "AB21 0YA"})

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "rix_browser")
        self.assertEqual(result.raw_payload["results_url"], "https://fuelquote.rix.co.uk/your-quote/123")
        self.assertAlmostEqual(
            result.price_per_liter, apply_vat(1.1035, DOMESTIC_VAT_RATE), places=4
        )

    def test_navigation_failure_falls_back_to_manual(self) -> None:
        page = FakePage(fail_goto=True)
        with _patcher(page):
            result = RixBrowserConnector().quote(SUPPLIER, 1000, {})
        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("Browser automation error", result.notes)


if __name__ == "__main__":
    unittest.main()
