"""Characterization tests for the synchronous Playwright connectors.

These drive Fuelsoft and Rix against a fake Playwright so the page-interaction
sequence and the result mapping (ok vs manual_action_required) are pinned. They
exist so the shared sync-browser base can be refactored without silently
changing supplier behaviour, which has no other offline coverage.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from oilwatch.connectors.suppliers.fuelsoft import HIDDEN_SECTIONS, FuelsoftConnector
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
    def __init__(self, url: str, body: object, *, json_raises: bool = False) -> None:
        self.url = url
        self._body = body
        self._json_raises = json_raises

    def json(self) -> object:
        if self._json_raises:
            raise ValueError("body was not JSON")
        return self._body

    def text(self) -> str:
        return str(self._body)


class FakeElement:
    """Just enough of a form control for the filling helpers."""

    def __init__(self, *, fail_fill: bool = False) -> None:
        self.filled: list[str] = []
        self.clicked = 0
        self._fail_fill = fail_fill

    def fill(self, value: str, **kwargs) -> None:
        if self._fail_fill:
            raise RuntimeError("element not interactable")
        self.filled.append(value)

    def click(self, **kwargs) -> None:
        self.clicked += 1


class FakeKeyboard:
    def __init__(self) -> None:
        self.pressed: list[str] = []

    def press(self, key: str) -> None:
        self.pressed.append(key)


class FakePage:
    """Minimal stand-in for a Playwright sync page."""

    def __init__(
        self,
        *,
        text: str = "",
        url: str = "https://example.test/quote",
        fail_goto: bool = False,
        on_wait_response: tuple[str, object] | None = None,
        elements: dict[str, FakeElement] | None = None,
        json_raises: bool = False,
    ) -> None:
        self.text = text
        self.url = url
        self.fail_goto = fail_goto
        self.calls: list[tuple] = []
        self.handlers: dict[str, object] = {}
        self.keyboard = FakeKeyboard()
        self._on_wait_response = on_wait_response
        self._json_raises = json_raises
        self._elements = elements or {}

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
            self.handlers["response"](FakeResponse(url, body, json_raises=self._json_raises))

    def wait_for_function(self, script: str, timeout: int | None = None) -> None:
        self.calls.append(("wait_for_function",))

    def query_selector(self, selector: str):
        return self._elements.get(selector)

    def click(self, selector: str, **kwargs) -> None:
        self.calls.append(("click", selector))
        element = self._elements.get(selector)
        if element is not None:
            element.click(**kwargs)

    def fill(self, selector: str, value: str, **kwargs) -> None:
        self.calls.append(("fill", selector, value))

    def select_option(self, selector: str, value: object = None, **kwargs) -> None:
        self.calls.append(("select_option", selector, value))

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


API_URL = "https://oilweb.example/JOil/fuelsoftapi/Quotes/deliveryschedules/quote/1"
QUOTE_URL = "https://oilweb.example/OnlineQuote.aspx"


class FuelsoftFormTests(unittest.TestCase):
    """The form-driving path, which the result-mapping tests never reached.

    These are the supplier's own quote form: if a selector or a field is dropped,
    the owner silently gets a manual quote instead of a price, so each step is
    pinned rather than left to the final fallback.
    """

    def _supplier(self, **config) -> dict:
        return {**SUPPLIER, "connector_config": {"quote_url": QUOTE_URL, **config}}

    def _quote(self, page: FakePage):
        context = {"postcode": "AB21 0YA", "email": "quote@example.test"}
        with _patcher(page):
            return FuelsoftConnector().quote(self._supplier(), 1000, context)

    def test_the_quote_form_is_filled_and_submitted(self) -> None:
        postcode, address, email = FakeElement(), FakeElement(), FakeElement()
        get_products, get_quote = FakeElement(), FakeElement()
        page = FakePage(
            on_wait_response=(API_URL, [{"PPL": 1.0878}]),
            elements={
                "#btnEnterAddressManually": FakeElement(),
                "#txtPostcode": postcode,
                "#txtDelAdd1": address,
                "#txtEmail": email,
                "#btnGetProducts": get_products,
                "#btnGetQuote": get_quote,
            },
        )

        result = self._quote(page)

        self.assertEqual(result.status, "ok")
        self.assertEqual(postcode.filled, ["AB21 0YA"])
        self.assertEqual(address.filled, ["Hatton of Fintray"])
        self.assertEqual(email.filled, ["quote@example.test"])
        self.assertEqual(get_products.clicked, 1)
        self.assertEqual(get_quote.clicked, 1)
        self.assertIn(("fill", "#txtQty", "1000"), page.calls)
        selected = {call[1]: call[2] for call in page.calls if call[0] == "select_option"}
        self.assertEqual(selected.get("#mainContent_lstProduct"), "003")  # KERO
        self.assertIn("#mainContent_lstDeliveryOption", selected)

    def test_the_hidden_wizard_sections_are_revealed(self) -> None:
        page = FakePage(on_wait_response=(API_URL, [{"PPL": 1.0}]))
        self._quote(page)

        revealed = " ".join(call[1] for call in page.calls if call[0] == "evaluate")
        for section in HIDDEN_SECTIONS:
            self.assertIn(section, revealed)

    def test_the_cookie_dialog_is_dismissed_when_it_appears(self) -> None:
        accept = FakeElement()
        page = FakePage(
            elements={"button:has-text('Accept all')": accept},
            on_wait_response=(API_URL, [{"PPL": 1.0}]),
        )
        self._quote(page)

        self.assertEqual(accept.clicked, 1)
        # Clicking the banner is enough; the Escape fallback must not also fire.
        self.assertEqual(page.keyboard.pressed, [])

    def test_a_field_that_will_not_accept_a_value_does_not_lose_the_quote(self) -> None:
        refuse, later = FakeElement(fail_fill=True), FakeElement()
        page = FakePage(
            elements={"#txtPostcode": refuse, "#txtDelAdd1": later},
            on_wait_response=(API_URL, [{"PPL": 1.0878}]),
        )

        result = self._quote(page)

        self.assertEqual(later.filled, ["Hatton of Fintray"])  # the rest was still attempted
        self.assertEqual(result.status, "ok")

    def test_a_non_json_quote_response_degrades_to_a_manual_quote(self) -> None:
        page = FakePage(on_wait_response=(API_URL, "<html>not json</html>"), json_raises=True)
        result = self._quote(page)

        self.assertEqual(result.status, "manual_action_required")
        self.assertIsNone(result.price_per_liter)

    def test_a_response_for_a_different_request_is_ignored(self) -> None:
        page = FakePage(on_wait_response=("https://oilweb.example/other/api", [{"PPL": 1.0}]))
        result = self._quote(page)

        self.assertEqual(result.status, "manual_action_required")


class FuelsoftParseRobustnessTests(unittest.TestCase):
    """A supplier sending "N/A" (or a bare object) must not crash the quote."""

    def test_junk_or_missing_prices_are_not_a_price(self) -> None:
        self.assertIsNone(FuelsoftConnector.parse_quote_response([{"PPL": "N/A"}]))
        self.assertIsNone(FuelsoftConnector.parse_quote_response([{"PPL": None}]))
        self.assertIsNone(FuelsoftConnector.parse_quote_response([]))

    def test_a_lone_quote_object_is_accepted(self) -> None:
        self.assertEqual(FuelsoftConnector.parse_quote_response({"PPL": 1.05}), 1.05)


if __name__ == "__main__":
    unittest.main()
