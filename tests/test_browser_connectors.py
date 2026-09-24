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
from oilwatch.identity import Contact
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


class LoginPage(FakeAsyncPage):
    """A login page that only shows the logout link once the form has been sent.

    The connector checks for an existing session *before* it fills anything, so a
    page offering that link from the start would never reach the submit at all.
    The wait for ``networkidle`` is allowed to time out, which is routine.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.session_checks = 0

    async def query_selector(self, selector: str):
        if "logout" in selector:
            self.session_checks += 1
            return FakeElement() if self.session_checks > 1 else None
        return await super().query_selector(selector)

    async def wait_for_load_state(self, *args, **kwargs) -> None:
        raise RuntimeError("networkidle never arrived")


class JourneyPage(FakeAsyncPage):
    """The quote journey's own form, which the connector is expected to drive.

    The live page carries a postcode, a volume, the two *required* selects a quote
    cannot be had without, and a Get Quote button. A fake built without them
    models a page the connector can never submit, and says so in a warning every
    run, so the quote tests are built on this rather than on a bare page.
    """

    def __init__(self, content: str = "", **kwargs) -> None:
        self.postcode = FakeElement()
        self.quantity = FakeElement(tag="INPUT")
        self.oil_type = FakeElement(tag="SELECT")
        self.tanker = FakeElement(tag="SELECT")
        self.button = FakeElement(tag="BUTTON")
        super().__init__(
            content=content,
            elements=[
                ("postcode", self.postcode),
                ("number", self.quantity),
                ("oil_type", self.oil_type),
                ("theTanker", self.tanker),
                ('has-text("Quote")', self.button),
            ],
            **kwargs,
        )


class BoilerJuiceConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = BoilerJuiceBrowserConnector()

    def test_extracts_price_and_applies_vat(self) -> None:
        page = JourneyPage(content="Heating oil today: £0.85 per litre")
        result = _quote(self.connector, page)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "boilerjuice_browser")
        # 0.85/L ex-VAT -> 0.8925/L inc-VAT; 1000L = £892.50.
        price_per_liter = result.price_per_liter
        assert price_per_liter is not None
        self.assertAlmostEqual(price_per_liter, 0.8925, places=4)
        total_price = result.total_price
        assert total_price is not None
        self.assertAlmostEqual(total_price, 892.5, places=2)

    def test_the_standard_delivery_total_is_read(self) -> None:
        """The standard option by its tag, not the cheapest of the options."""
        content = (
            '<p data-test="ppl_delivery5_value">118.84 ppl</p>'
            '<p data-test="price_delivery5_value">£1,268.20</p>'
            '<p data-test="ppl_standard_value">117.64 ppl</p>'
            '<p data-test="price_standard_value">£1,248.20</p>'
        )
        self.assertEqual(BoilerJuiceBrowserConnector.parse_inclusive_total(content), 1248.20)

    def test_the_price_comes_from_the_standard_total(self) -> None:
        """The inclusive total carries the service charge the ppl omits, so the
        quote is read from it rather than the headline ppl."""
        page = JourneyPage(content='<p data-test="price_standard_value">£1,195.70</p>')
        result = _quote(self.connector, page)

        self.assertEqual(result.status, "ok")
        price_per_liter = result.price_per_liter
        assert price_per_liter is not None
        self.assertAlmostEqual(price_per_liter, 1.1957, places=4)
        total_price = result.total_price
        assert total_price is not None
        self.assertAlmostEqual(total_price, 1195.70, places=2)

    def test_the_required_selects_are_answered_or_the_form_never_submits(self) -> None:
        """The journey quotes nothing until the oil type and the tanker are chosen.

        Both are required and both open on a placeholder option, so the browser
        refuses the submit, the page sits there unchanged, and the run reads as
        ``no_price_found`` and nothing else - which is what it did on every
        attempt until these two were answered (checked live 2026-09-22).
        """
        page = JourneyPage(content="Heating oil today: £0.85 per litre")

        result = _quote(self.connector, page)

        self.assertEqual(page.oil_type.selected, [BoilerJuiceBrowserConnector.OIL_TYPE_VALUE])
        self.assertEqual(page.tanker.selected, [BoilerJuiceBrowserConnector.TANKER_VALUE])
        self.assertEqual(result.status, "ok", "answering the selects is what lets the form submit")

    def test_no_price_is_manual_not_fabricated(self) -> None:
        page = JourneyPage(content="<html>no price here</html>")
        result = _quote(self.connector, page)
        self.assertEqual(result.status, "manual_action_required")
        self.assertIsNone(result.price_per_liter)

    def test_a_field_that_will_not_take_a_value_does_not_error_the_quote(self) -> None:
        """The live buy-now form sits in a collapsed accordion, so its postcode
        is hidden. A field that refuses its value must not abort the run as a
        browser error — it degrades to the manual note."""
        page = JourneyPage()
        page.postcode.fill_raises = True
        result = _quote(self.connector, page)
        self.assertEqual(result.status, "manual_action_required")

    def test_login_reports_already_logged_in(self) -> None:
        page = FakeAsyncPage(elements=[("logout", FakeElement())])
        self.assertTrue(asyncio.run(self.connector.login(page, "owner@example.test", "pw")))

    def test_login_reports_a_missing_form_as_the_reason(self) -> None:
        with self.assertRaises(RuntimeError) as raised:
            asyncio.run(self.connector.login(FakeAsyncPage(), "owner@example.test", "pw"))

        self.assertIn("sign-in form was not found", str(raised.exception))

    def test_login_succeeds_even_when_networkidle_times_out(self) -> None:
        email, password, button = FakeElement(), FakeElement(), FakeElement(tag="BUTTON")
        page = LoginPage(
            elements=[("email", email), ("password", password), ('has-text("Login")', button)]
        )

        self.assertTrue(asyncio.run(self.connector.login(page, "owner@example.test", "hunter2")))

        self.assertEqual(email.filled, ["owner@example.test"])
        self.assertEqual(button.clicked, 1)
        self.assertEqual(page.session_checks, 2)  # once before the form, once after

    def test_login_reports_the_credentials_message_as_the_reason(self) -> None:
        page = FakeAsyncPage(
            elements=[
                ("email", FakeElement()),
                ("password", FakeElement()),
                ('has-text("Login")', FakeElement(tag="BUTTON")),
                ("alert-danger", FakeElement(text="Sorry, that email or password is incorrect")),
            ]
        )

        with self.assertRaises(RuntimeError) as raised:
            asyncio.run(self.connector.login(page, "owner@example.test", "wrong"))

        self.assertIn("that email or password is incorrect", str(raised.exception))

    def test_an_unrelated_error_banner_still_means_the_login_failed(self) -> None:
        """Any error element means the sign-in did not take, whatever it says."""
        page = FakeAsyncPage(
            elements=[
                ("email", FakeElement()),
                ("password", FakeElement()),
                ('has-text("Login")', FakeElement(tag="BUTTON")),
                ("alert-danger", FakeElement(text="Please try again later")),
            ]
        )

        with self.assertRaises(RuntimeError) as raised:
            asyncio.run(self.connector.login(page, "owner@example.test", "pw"))

        self.assertIn("Please try again later", str(raised.exception))

    def test_a_login_that_breaks_reports_the_reason(self) -> None:
        page = FakeAsyncPage()

        async def boom(url: str, **kwargs) -> None:
            raise RuntimeError("no such window")

        page.goto = boom

        with self.assertRaises(RuntimeError) as raised:
            asyncio.run(self.connector.login(page, "owner@example.test", "pw"))

        self.assertIn("no such window", str(raised.exception))

    def test_the_quantity_dropdown_is_searched_for_the_right_option(self) -> None:
        cases = {
            "an option list that has to be searched": [
                FakeElement(attributes={"value": "500"}),
                FakeElement(attributes={"value": "litres_1000"}),
            ],
            "an empty option list": [],
        }
        for label, options in cases.items():
            with self.subTest(case=label):
                quantity = FakeElement(tag="SELECT", options=options)
                page = FakeAsyncPage(
                    content="Heating oil today: £0.85 per litre", elements=[("quantity", quantity)]
                )

                result = _quote(self.connector, page)

                self.assertEqual(result.status, "ok")
                self.assertEqual(quantity.selected, ["litres_1000"] if options else [])

    def test_the_price_element_scan_skips_what_carries_no_price(self) -> None:
        page = FakeAsyncPage(
            content="<html>Call us for today's price</html>",
            selector_all=[
                (
                    "price",
                    [
                        FakeElement(text=""),
                        FakeElement(text="POA"),
                        FakeElement(text="£0.85 per litre"),
                    ],
                )
            ],
        )

        result = _quote(self.connector, page)

        self.assertEqual(result.status, "ok")
        # 0.85/L ex-VAT -> 0.8925/L inc-VAT.
        price_per_liter = result.price_per_liter
        assert price_per_liter is not None
        self.assertAlmostEqual(price_per_liter, 0.8925, places=4)

    def test_a_comma_delivery_total_is_not_read_as_a_price_per_litre(self) -> None:
        """£1,195.70 is a delivery total, not £1.957 a litre.

        The two unanchored patterns — ``price.*?£?(\\d+\\.\\d{2})`` and the same
        for ``total`` — matched the digits *after* the comma, since the comma is
        never parsed and ``\\d+`` simply starts at "195". That normalised to
        £1.957/L and was then lifted another 5%, entering the database as an
        ``ok`` quote. Both patterns are gone; every one left is anchored to a
        per-litre unit or a two-word label.
        """
        page = JourneyPage(content="<html>Total price £1,195.70</html>")

        result = _quote(self.connector, page)

        self.assertIsNone(result.price_per_liter, "a total is not a price per litre")
        self.assertEqual(result.status, "manual_action_required")

    def test_an_extraction_error_leaves_the_quote_for_manual_action(self) -> None:
        page = FakeAsyncPage(content="whatever")

        async def boom() -> str:
            raise RuntimeError("page detached")

        page.content = boom

        result = _quote(self.connector, page)

        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("could not extract", result.notes)

    def test_a_broken_browser_is_recorded_as_an_error_quote(self) -> None:
        page = FakeAsyncPage()

        async def boom(url: str, **kwargs) -> None:
            raise RuntimeError("browser died")

        page.goto = boom

        result = _quote(self.connector, page)

        self.assertEqual(result.status, "error")
        self.assertIn("browser died", result.notes)


class ValueOilsBrowserConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = ValueOilsBrowserConnector()

    #: The Quick Quote result, as ValueOils renders it (text, tags stripped).
    QUICK_QUOTE = (
        "Delivery Options based on postcode AB21 0YA Quantity: 1000 (litres) "
        "Delivery Option Fuel ppl ex. VAT Total You Pay "
        "Standard Delivery - Estimated Delivery by Monday 28th Sep 2026 "
        "111.10p £1,187.55 Buy Now "
        "Express Delivery 7 (+£29.90) Delivery by Wednesday 23rd Sep 2026 "
        "111.10p £1,217.45 Buy Now"
    )

    def _page(self, *, content: str | None = None, email: FakeElement | None = None) -> FakeAsyncPage:
        return FakeAsyncPage(
            content=self.QUICK_QUOTE if content is None else content,
            elements=[
                ("sgcPriceChecker_txtQuotePostcode", FakeElement()),
                ("sgcPriceChecker_txtQuoteEmail", email or FakeElement()),
                ("sgcPriceChecker_txtQuantity", FakeElement()),
                ("sgcPriceChecker_btnShowPrices", FakeElement(tag="INPUT")),
            ],
        )

    def test_reads_the_standard_delivery_total(self) -> None:
        postcode, quantity, button = FakeElement(), FakeElement(), FakeElement(tag="INPUT")
        page = FakeAsyncPage(
            content=self.QUICK_QUOTE,
            elements=[
                ("sgcPriceChecker_txtQuotePostcode", postcode),
                ("sgcPriceChecker_txtQuoteEmail", FakeElement()),
                ("sgcPriceChecker_txtQuantity", quantity),
                ("sgcPriceChecker_btnShowPrices", button),
            ],
        )

        result = _quote(self.connector, page)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "valueoils_browser")
        # £1,187.55 for 1000L is already inc VAT and commission -> £1.1875/L,
        # and the stored total follows from it (the app-wide invariant).
        self.assertEqual(result.raw_payload["standard_delivery_total"], 1187.55)
        price_per_liter = result.price_per_liter
        assert price_per_liter is not None
        self.assertAlmostEqual(price_per_liter, 1.1875, places=4)
        total_price = result.total_price
        assert total_price is not None
        self.assertAlmostEqual(total_price, price_per_liter * 1000, places=2)
        self.assertEqual(postcode.filled, ["AB21 0YA"])
        self.assertEqual(quantity.filled, ["1000"])
        self.assertEqual(button.clicked, 1)

    def test_it_reads_the_standard_option_not_express(self) -> None:
        result = _quote(self.connector, self._page())
        self.assertEqual(result.raw_payload["standard_delivery_total"], 1187.55)
        price_per_liter = result.price_per_liter
        assert price_per_liter is not None
        self.assertNotAlmostEqual(price_per_liter, 1.2175, places=4)  # not Express 7

    def test_falls_back_to_http_when_no_price_is_shown(self) -> None:
        sentinel = object()
        with patch.object(
            ValueOilsBrowserConnector, "_fallback_to_http", new=AsyncMock(return_value=sentinel)
        ) as fallback:
            result = _quote(self.connector, FakeAsyncPage(content="<html>no price</html>"))
        fallback.assert_awaited_once()
        self.assertIs(result, sentinel)

    def test_login_fills_and_submits_the_form_when_one_is_offered(self) -> None:
        email, password, button = FakeElement(), FakeElement(), FakeElement(tag="BUTTON")
        page = FakeAsyncPage(
            elements=[("email", email), ("password", password), ('has-text("Login")', button)]
        )

        self.assertTrue(asyncio.run(self.connector.login(page, "owner@example.test", "hunter2")))

        self.assertEqual(email.filled, ["owner@example.test"])
        self.assertEqual(password.filled, ["hunter2"])
        self.assertEqual(button.clicked, 1)
        self.assertEqual(page.goto_urls, [self.connector.login_url])

    def test_the_quick_quote_email_comes_from_the_contact(self) -> None:
        """The form asks for an address to quote to; it is the configured one."""
        email = FakeElement()
        page = self._page(email=email)

        with patch(
            "oilwatch.connectors.suppliers.valueoils_browser.load_contact",
            return_value=Contact(email="owner@example.test"),
        ):
            result = _quote(self.connector, page)

        self.assertEqual(email.filled, ["owner@example.test"])
        self.assertEqual(result.status, "ok")

    def test_a_missing_form_falls_back_to_http_with_the_error(self) -> None:
        sentinel = object()
        with patch.object(
            ValueOilsBrowserConnector, "_fallback_to_http", new=AsyncMock(return_value=sentinel)
        ) as fallback:
            result = _quote(self.connector, FakeAsyncPage(content=self.QUICK_QUOTE))

        self.assertIs(result, sentinel)
        awaited = fallback.await_args
        assert awaited is not None
        self.assertIn("postcode", awaited.args[3])

    def test_a_broken_browser_falls_back_to_http_with_the_error(self) -> None:
        sentinel = object()
        page = FakeAsyncPage()

        async def boom(url: str, **kwargs) -> None:
            raise RuntimeError("browser died")

        page.goto = boom
        with patch.object(
            ValueOilsBrowserConnector, "_fallback_to_http", new=AsyncMock(return_value=sentinel)
        ) as fallback:
            result = _quote(self.connector, page)

        self.assertIs(result, sentinel)
        awaited = fallback.await_args
        assert awaited is not None
        self.assertEqual(awaited.args[3], "browser died")

    def test_no_delivery_table_falls_back_without_blaming_the_browser(self) -> None:
        sentinel = object()
        with patch.object(
            ValueOilsBrowserConnector, "_fallback_to_http", new=AsyncMock(return_value=sentinel)
        ) as fallback:
            result = _quote(self.connector, self._page(content="<html>no delivery table</html>"))

        self.assertIs(result, sentinel)
        awaited = fallback.await_args
        assert awaited is not None
        self.assertEqual(len(awaited.args), 3)  # no browser error to report


class HomeFuelsDirectBrowserConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = HomeFuelsDirectBrowserConnector()

    def test_reads_the_live_price_span_and_applies_5pc_vat(self) -> None:
        """The live figure is a public, server-rendered pence value in a span.

        The page reads "…<span id=\"currentLivePrice\">112.87</span> pence /
        litre". It is read directly rather than by driving the enquiry form,
        whose postcode control is not interactable.
        """
        page = FakeAsyncPage(elements=[("#currentLivePrice", FakeElement(text="112.87"))])

        result = _quote(self.connector, page)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "homefuels_direct_browser")
        # 112.87p/L ex-VAT -> £1.1287 -> +5% domestic VAT -> £1.1851/L, and the
        # stored total follows from the per-litre price (the app-wide invariant).
        price_per_liter = result.price_per_liter
        assert price_per_liter is not None
        self.assertAlmostEqual(price_per_liter, 1.1851, places=4)
        total_price = result.total_price
        assert total_price is not None
        self.assertAlmostEqual(total_price, 1185.1, places=2)
        self.assertAlmostEqual(total_price, price_per_liter * 1000, places=2)

    def test_falls_back_to_http_when_no_price_is_shown(self) -> None:
        sentinel = object()
        with patch.object(
            HomeFuelsDirectBrowserConnector, "_fallback_to_http", new=AsyncMock(return_value=sentinel)
        ) as fallback:
            result = _quote(self.connector, FakeAsyncPage(content="<html>no price</html>"))
        fallback.assert_awaited_once()
        self.assertIs(result, sentinel)

    def test_login_fills_and_submits_the_form_when_one_is_offered(self) -> None:
        email, password, button = FakeElement(), FakeElement(), FakeElement(tag="BUTTON")
        page = FakeAsyncPage(
            elements=[("email", email), ("password", password), ('has-text("Login")', button)]
        )

        self.assertTrue(asyncio.run(self.connector.login(page, "owner@example.test", "hunter2")))

        self.assertEqual(email.filled, ["owner@example.test"])
        self.assertEqual(password.filled, ["hunter2"])
        self.assertEqual(button.clicked, 1)
        self.assertEqual(page.goto_urls, [self.connector.login_url])

    def test_a_broken_browser_falls_back_to_http_with_the_error(self) -> None:
        sentinel = object()
        page = FakeAsyncPage(content="<html>no price</html>")

        async def boom(url: str, **kwargs) -> None:
            raise RuntimeError("browser died")

        page.goto = boom
        with patch.object(
            HomeFuelsDirectBrowserConnector, "_fallback_to_http", new=AsyncMock(return_value=sentinel)
        ) as fallback:
            result = _quote(self.connector, page)

        self.assertIs(result, sentinel)
        awaited = fallback.await_args
        assert awaited is not None
        self.assertEqual(awaited.args[3], "browser died")

    def test_a_span_without_a_figure_falls_back_to_http(self) -> None:
        """A 'Loading…' span must not be mistaken for a price."""
        sentinel = object()
        page = FakeAsyncPage(elements=[("#currentLivePrice", FakeElement(text="Loading…"))])
        with patch.object(
            HomeFuelsDirectBrowserConnector, "_fallback_to_http", new=AsyncMock(return_value=sentinel)
        ) as fallback:
            result = _quote(self.connector, page)

        self.assertIs(result, sentinel)
        awaited = fallback.await_args
        assert awaited is not None
        self.assertEqual(len(awaited.args), 3)  # no browser error to report


if __name__ == "__main__":
    unittest.main()
