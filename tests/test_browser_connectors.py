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

    def test_login_succeeds_even_when_networkidle_times_out(self) -> None:
        email, password, button = FakeElement(), FakeElement(), FakeElement(tag="BUTTON")
        page = LoginPage(
            elements=[("email", email), ("password", password), ('has-text("Login")', button)]
        )

        self.assertTrue(asyncio.run(self.connector.login(page, "owner@example.test", "hunter2")))

        self.assertEqual(email.filled, ["owner@example.test"])
        self.assertEqual(button.clicked, 1)
        self.assertEqual(page.session_checks, 2)  # once before the form, once after

    def test_login_reports_an_invalid_credentials_message_as_failure(self) -> None:
        page = FakeAsyncPage(
            elements=[
                ("email", FakeElement()),
                ("password", FakeElement()),
                ('has-text("Login")', FakeElement(tag="BUTTON")),
                ("alert-danger", FakeElement(text="Sorry, that email or password is incorrect")),
            ]
        )

        self.assertFalse(asyncio.run(self.connector.login(page, "owner@example.test", "wrong")))

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

        self.assertFalse(asyncio.run(self.connector.login(page, "owner@example.test", "pw")))

    def test_a_login_that_breaks_does_not_escape(self) -> None:
        page = FakeAsyncPage()

        async def boom(url: str, **kwargs) -> None:
            raise RuntimeError("no such window")

        page.goto = boom

        self.assertFalse(asyncio.run(self.connector.login(page, "owner@example.test", "pw")))

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
        self.assertAlmostEqual(result.price_per_liter, 0.85, places=4)

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

    def test_the_quote_form_email_comes_from_the_contact(self) -> None:
        """The form asks for an address to quote to; it is the configured one."""
        email = FakeElement()
        page = FakeAsyncPage(
            content="Heating Oil Kerosene 103.90p per litre", elements=[("email", email)]
        )

        with patch(
            "oilwatch.connectors.suppliers.valueoils_browser.load_contact",
            return_value=Contact(email="owner@example.test"),
        ):
            result = _quote(self.connector, page)

        self.assertEqual(email.filled, ["owner@example.test"])
        self.assertEqual(result.status, "ok")

    def test_a_dropdown_that_will_not_take_a_value_does_not_lose_the_quote(self) -> None:
        usage = FakeElement(tag="SELECT", select_raises=True)
        fuel = FakeElement(tag="SELECT", select_raises=True)
        page = FakeAsyncPage(
            content="Heating Oil Kerosene 103.90p per litre",
            elements=[("usage_type", usage), ("fuel_type", fuel)],
        )

        result = _quote(self.connector, page)

        self.assertEqual(result.status, "ok")
        self.assertEqual(usage.selected, [])
        self.assertEqual(fuel.selected, [])

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
        self.assertEqual(fallback.await_args.args[3], "browser died")

    def test_an_extraction_error_falls_back_without_blaming_the_browser(self) -> None:
        sentinel = object()
        page = FakeAsyncPage(content="whatever")

        async def boom() -> str:
            raise RuntimeError("page detached")

        page.content = boom
        with patch.object(
            ValueOilsBrowserConnector, "_fallback_to_http", new=AsyncMock(return_value=sentinel)
        ) as fallback:
            result = _quote(self.connector, page)

        self.assertIs(result, sentinel)
        self.assertEqual(len(fallback.await_args.args), 3)  # no browser error to report

    def test_a_price_already_in_pounds_is_not_converted_again(self) -> None:
        """The >100 rule is a heuristic, so a pounds price has to pass through."""
        page = FakeAsyncPage(content="Our price today: £1.55 per litre")

        result = _quote(self.connector, page)

        self.assertEqual(result.status, "ok")
        self.assertAlmostEqual(result.price_per_liter, 1.55, places=4)


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
        self.assertEqual(fallback.await_args.args[3], "browser died")

    def test_the_price_can_come_from_a_price_element(self) -> None:
        """The page text often carries no price; the price elements do."""
        page = FakeAsyncPage(
            content="<html>Call us for today's price</html>",
            selector_all=[
                (
                    "price",
                    [
                        FakeElement(text=""),  # an empty container
                        FakeElement(text="POA"),  # a container with no price in it
                        FakeElement(text="£1.23 per litre"),
                    ],
                )
            ],
        )

        result = _quote(self.connector, page)

        self.assertEqual(result.status, "ok")
        self.assertAlmostEqual(result.price_per_liter, 1.23, places=4)

    def test_an_extraction_error_falls_back_without_blaming_the_browser(self) -> None:
        sentinel = object()
        page = FakeAsyncPage(content="whatever")

        async def boom() -> str:
            raise RuntimeError("page detached")

        page.content = boom
        with patch.object(
            HomeFuelsDirectBrowserConnector, "_fallback_to_http", new=AsyncMock(return_value=sentinel)
        ) as fallback:
            result = _quote(self.connector, page)

        self.assertIs(result, sentinel)
        self.assertEqual(len(fallback.await_args.args), 3)  # no browser error to report


if __name__ == "__main__":
    unittest.main()
