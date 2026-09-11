"""The per-supplier registration flows, against a fake async page.

_setup and _close are stubbed; everything between them - cookie handling, the
already-registered check, field filling, the register button, and reading the
outcome back out of the page - is the code under test.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from oilwatch.auto_register import AccountRegistrar
from tests.fake_async_page import FakeAsyncPage, FakeElement

FLOWS = ["register_scottish_fuels", "register_valueoils", "register_homefuels_direct"]
ARGS = ("Wayne", "owner@example.test", "01224 000000", "Hatton of Fintry", "AB21 0YA")

REGISTER_BUTTON = ('name="register"', FakeElement(tag="BUTTON"))


class BoomPage(FakeAsyncPage):
    async def goto(self, url: str, **kwargs) -> None:
        raise RuntimeError("navigation failed")


class RegistrarFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registrar = AccountRegistrar()
        for patcher in (
            patch.object(AccountRegistrar, "_close", new=AsyncMock()),
            patch("oilwatch.auto_register.store_supplier_credentials"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _register(self, flow: str, page: FakeAsyncPage) -> dict:
        with patch.object(AccountRegistrar, "_setup", new=AsyncMock(return_value=page)):
            return asyncio.run(getattr(self.registrar, flow)(*ARGS))

    def test_an_existing_session_is_already_registered(self) -> None:
        for flow in FLOWS:
            with self.subTest(flow=flow):
                page = FakeAsyncPage(elements=[("logout", FakeElement())])
                self.assertEqual(self._register(flow, page)["status"], "already_registered")

    def test_an_existing_account_error_is_recognised(self) -> None:
        error = FakeElement(text="An account already exists for this email address")
        for flow in FLOWS:
            with self.subTest(flow=flow):
                page = FakeAsyncPage(elements=[REGISTER_BUTTON, (".error", error)])
                self.assertEqual(self._register(flow, page)["status"], "already_registered")

    def test_a_submitted_form_without_confirmation_needs_review(self) -> None:
        for flow in FLOWS:
            with self.subTest(flow=flow):
                page = FakeAsyncPage(elements=[REGISTER_BUTTON])
                self.assertEqual(self._register(flow, page)["status"], "manual_review")

    def test_no_register_button_needs_review(self) -> None:
        for flow in FLOWS:
            with self.subTest(flow=flow):
                self.assertEqual(self._register(flow, FakeAsyncPage())["status"], "manual_review")

    def test_a_navigation_failure_needs_review(self) -> None:
        for flow in FLOWS:
            with self.subTest(flow=flow):
                result = self._register(flow, BoomPage())
                self.assertEqual(result["status"], "manual_review")
                self.assertIn("encountered issues", result["message"])

    def test_scottish_fuels_reports_a_confirmed_registration(self) -> None:
        page = FakeAsyncPage(
            elements=[REGISTER_BUTTON, (".woocommerce-message", FakeElement(text="Your account has been created"))]
        )
        self.assertEqual(self._register("register_scottish_fuels", page)["status"], "registered")

    def test_a_register_click_that_raises_ends_in_the_same_place(self) -> None:
        """A click that navigates away raises here; it is not a failure."""
        for flow in FLOWS:
            with self.subTest(flow=flow):
                button = FakeElement(tag="BUTTON", click_raises=True)
                page = FakeAsyncPage(elements=[('name="register"', button)])

                self.assertEqual(self._register(flow, page)["status"], "manual_review")

    def test_an_error_banner_that_says_neither_exists_nor_success_needs_review(self) -> None:
        for flow in FLOWS:
            with self.subTest(flow=flow):
                error = FakeElement(text="Please enter a valid postcode")
                page = FakeAsyncPage(elements=[REGISTER_BUTTON, (".error", error)])

                self.assertEqual(self._register(flow, page)["status"], "manual_review")

    def test_scottish_fuels_follows_the_register_link_when_one_is_offered(self) -> None:
        link, button = FakeElement(), FakeElement(tag="BUTTON")
        page = FakeAsyncPage(elements=[('a:has-text("Sign Up")', link), ('name="register"', button)])

        self._register("register_scottish_fuels", page)

        self.assertEqual(link.clicked, 1)
        self.assertEqual(button.clicked, 1)  # the flow carried on to the submit

    def test_scottish_fuels_survives_a_register_link_that_will_not_click(self) -> None:
        link, button = FakeElement(click_raises=True), FakeElement(tag="BUTTON")
        page = FakeAsyncPage(elements=[('a:has-text("Sign Up")', link), ('name="register"', button)])

        self.assertEqual(self._register("register_scottish_fuels", page)["status"], "manual_review")

        self.assertEqual(button.clicked, 1)  # a dead link does not stop the registration

    def test_scottish_fuels_fills_the_first_selector_set_without_falling_back(self) -> None:
        username = FakeElement()
        page = FakeAsyncPage(elements=[('input[name="username"]', username), REGISTER_BUTTON])

        self._register("register_scottish_fuels", page)

        self.assertEqual(username.filled, ["wayne"])

    def test_a_banner_with_no_text_asks_for_a_review(self) -> None:
        """A banner that renders empty says nothing either way.

        It used to fall through and leave the status at its initial pending,
        which is neither a success nor a flagged review.
        """
        for flow in FLOWS:
            with self.subTest(flow=flow):
                page = FakeAsyncPage(
                    elements=[REGISTER_BUTTON, (".woocommerce-error", FakeElement(text=""))]
                )

                result = self._register(flow, page)

                self.assertEqual(result["status"], "manual_review")
                self.assertIn("no message", result["message"])


class CookieBannerTests(unittest.TestCase):
    """Consent banners are dismissed, and one that will not click is not fatal."""

    def setUp(self) -> None:
        self.registrar = AccountRegistrar()

    def _accept(self, page: FakeAsyncPage) -> None:
        asyncio.run(self.registrar._accept_cookies(page))

    def test_a_banner_that_will_not_click_falls_through_to_the_next_selector(self) -> None:
        stubborn = FakeElement(tag="BUTTON", click_raises=True)
        later = FakeElement(tag="BUTTON")
        page = FakeAsyncPage(
            elements=[
                ('button:has-text("Accept All")', stubborn),
                (".iubenda-cs-accept-btn", later),
            ]
        )

        self._accept(page)

        self.assertEqual(later.clicked, 1)

    def test_the_close_button_is_used_when_there_is_no_accept_button(self) -> None:
        close_btn = FakeElement(tag="BUTTON")
        page = FakeAsyncPage(elements=[(".iubenda-cs-close", close_btn)])

        self._accept(page)

        self.assertEqual(close_btn.clicked, 1)

    def test_a_close_button_that_will_not_click_is_not_fatal(self) -> None:
        page = FakeAsyncPage(elements=[(".iubenda-cs-close", FakeElement(tag="BUTTON", click_raises=True))])

        self._accept(page)  # must not raise


if __name__ == "__main__":
    unittest.main()
