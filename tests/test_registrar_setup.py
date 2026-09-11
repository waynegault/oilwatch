"""AccountRegistrar's browser setup/teardown and the module entry point."""

from __future__ import annotations

import asyncio
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from oilwatch.auto_register import AccountRegistrar, register_all
from tests.fake_async_page import FakeAsyncPage, FakeElement


class FakeContext:
    def __init__(self, page) -> None:
        self._page = page
        self.closed = False

    async def new_page(self):
        return self._page

    async def close(self) -> None:
        self.closed = True

    async def route(self, pattern, handler) -> None:
        return None


class FakeBrowser:
    def __init__(self, context) -> None:
        self._context = context
        self.closed = False

    async def new_context(self, **kwargs):
        return self._context

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, browser) -> None:
        self._browser = browser

    async def launch(self, **kwargs):
        return self._browser


class FakePlaywright:
    def __init__(self, browser) -> None:
        self.chromium = FakeChromium(browser)
        self.stopped = False

    async def start(self):
        return self

    async def stop(self) -> None:
        self.stopped = True


def _wired():
    page = FakeAsyncPage()
    context = FakeContext(page)
    page.context = context  # _close closes the page's own context
    browser = FakeBrowser(context)
    return AccountRegistrar(), page, context, FakePlaywright(browser)


class SetupTests(unittest.TestCase):
    def test_setup_launches_and_returns_the_page(self) -> None:
        registrar, page, _, playwright = _wired()
        with patch("oilwatch.auto_register.async_playwright", return_value=playwright):
            returned = asyncio.run(registrar._setup())

        self.assertIs(returned, page)
        self.assertIs(registrar._page, page)

    def test_close_releases_the_context_and_playwright(self) -> None:
        registrar, _, context, playwright = _wired()
        with patch("oilwatch.auto_register.async_playwright", return_value=playwright):
            asyncio.run(registrar._setup())
            asyncio.run(registrar._close())

        self.assertTrue(context.closed)
        self.assertTrue(playwright.stopped)
        self.assertIsNone(registrar._page)

    def test_close_is_safe_when_nothing_was_opened(self) -> None:
        asyncio.run(AccountRegistrar()._close())


class ResultTests(unittest.TestCase):
    def test_save_results_writes_the_json(self) -> None:
        registrar = AccountRegistrar()
        registrar._results = [{"supplier": "ValueOils", "status": "registered"}]

        with tempfile.TemporaryDirectory() as tmp:
            path = registrar.save_results(Path(tmp) / "nested" / "results.json")
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload, registrar._results)

    def test_a_confirmed_error_message_counts_as_registered(self) -> None:
        registrar = AccountRegistrar()
        page = FakeAsyncPage(
            elements=[
                ('name="register"', FakeElement(tag="BUTTON")),
                (".error", FakeElement(text="Your account has been created")),
            ]
        )
        with (
            patch.object(AccountRegistrar, "_setup", new=AsyncMock(return_value=page)),
            patch.object(AccountRegistrar, "_close", new=AsyncMock()),
            patch("oilwatch.auto_register.store_supplier_credentials"),
        ):
            result = asyncio.run(
                registrar.register_scottish_fuels("Wayne", "a@b.c", "01224", "Hatton", "AB21 0YA")
            )

        self.assertEqual(result["status"], "registered")


class EntryPointTests(unittest.TestCase):
    def test_register_all_uses_the_configured_contact_as_defaults(self) -> None:
        contact = types.SimpleNamespace(name="Wayne", email="owner@example.test", phone="01224", postcode="AB21 0YA")
        registrar = MagicMock()
        registrar.register_all_suppliers = AsyncMock(return_value=[{"supplier": "ValueOils"}])

        with (
            patch("oilwatch.auto_register.load_contact", return_value=contact),
            patch("oilwatch.auto_register.AccountRegistrar", return_value=registrar) as registrar_cls,
        ):
            results = asyncio.run(register_all())

        self.assertEqual(results, [{"supplier": "ValueOils"}])
        registrar_cls.assert_called_once_with(headless=True)
        registrar.register_all_suppliers.assert_awaited_once_with(
            # The address is the caller's, not the contact's: the registration
            # forms ask for a name, an email and a password, so nothing supplies
            # one and the literal that used to sit here was doing no work.
            "Wayne",
            "owner@example.test",
            "01224",
            "",
            "AB21 0YA",
        )

    def test_an_explicit_phone_is_kept_even_when_empty(self) -> None:
        contact = types.SimpleNamespace(name="Wayne", email="a@b.c", phone="01224", postcode="AB21 0YA")
        registrar = MagicMock()
        registrar.register_all_suppliers = AsyncMock(return_value=[])

        with (
            patch("oilwatch.auto_register.load_contact", return_value=contact),
            patch("oilwatch.auto_register.AccountRegistrar", return_value=registrar),
        ):
            asyncio.run(register_all(phone="", headless=False))

        self.assertEqual(registrar.register_all_suppliers.await_args.args[2], "")


if __name__ == "__main__":
    unittest.main()
