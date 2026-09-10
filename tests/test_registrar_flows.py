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


if __name__ == "__main__":
    unittest.main()
