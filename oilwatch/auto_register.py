"""Automated supplier account registration: one flow, three suppliers' forms.

The suppliers' registration pages differ in their selectors, their field names
and what their banners say. They do not differ in what has to happen: navigate,
dismiss the consent banner, notice an existing session, fill the form, submit,
and read the outcome out of whatever banner the page left behind. That is the
flow below, and each supplier is described by a :class:`RegistrationForm`.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, Page, Playwright, async_playwright

from oilwatch.credentials import (
    generate_supplier_password,
    get_credential_manager,
    store_supplier_credentials,
)
from oilwatch.identity import load_contact
from oilwatch.logging_setup import get_logger

log = get_logger("auto_register")


@dataclass(frozen=True)
class RegistrationForm:
    """What differs between the suppliers' registration pages.

    ``fields`` maps a selector to a value by name — ``first_name``, ``email`` or
    ``password`` — so the same form description works for every supplier.
    ``error_says_success`` marks the site that reports a *successful* signup
    through its error banner; every other site's banner means trouble.
    """

    key: str
    name: str
    login_url: str
    register_button: str
    error_banner: str
    fields: tuple[tuple[str, str], ...]
    success_banner: str | None = None
    register_link: str | None = None
    fallback_fields: tuple[tuple[str, str], ...] = ()
    error_says_success: bool = False
    settle_ms: int = 0


# Fill-ins for the shop-style forms: ValueOils and HomeFuels Direct ask the same
# questions with the same names.
_SHOP_FIELDS = (
    ('input[name="email"]', "email"),
    ('input[name="password"]', "password"),
    ('input[name="confirm_password"]', "password"),
    ('input[id="reg_email"]', "email"),
    ('input[id="reg_password"]', "password"),
)

SCOTTISH_FUELS = RegistrationForm(
    key="scottish_fuels",
    name="Scottish Fuels",
    login_url="https://scottishfuels.co.uk/my-account/",
    register_button=(
        'button[name="register"], input[name="register"], '
        'button:has-text("Register"), input[value*="Register"], '
        'button:has-text("Sign Up")'
    ),
    error_banner=".woocommerce-error, .error, .alert",
    success_banner=".woocommerce-message, .success, .alert-success",
    register_link='a:has-text("Register"), a:has-text("Sign Up")',
    error_says_success=True,
    fields=(
        ('input[name="username"]', "first_name"),
        ('input[name="email"]', "email"),
        ('input[name="password"]', "password"),
        ('input[name="account_email"]', "email"),
        ('input[name="account_password"]', "password"),
    ),
    fallback_fields=(
        ('input[id="reg_email"]', "email"),
        ('input[id="reg_password"]', "password"),
        ('input[type="email"]', "email"),
        ('input[type="password"]', "password"),
    ),
)

VALUEOILS = RegistrationForm(
    key="valueoils",
    name="ValueOils",
    login_url="https://www.valueoils.com/my-account/",
    register_button=(
        'button:has-text("Register"), input[value*="Register"], '
        'button:has-text("Sign Up"), button[name="register"]'
    ),
    error_banner=".error, .alert-danger, [class*='error'], .woocommerce-error",
    settle_ms=2000,
    fields=_SHOP_FIELDS,
)

HOMEFUELS_DIRECT = RegistrationForm(
    key="homefuels_direct",
    name="HomeFuels Direct",
    login_url="https://homefuelsdirect.co.uk/my-account/",
    register_button=(
        'button:has-text("Register"), input[value*="Register"], '
        'button:has-text("Sign Up"), button[name="register"]'
    ),
    error_banner=".error, .alert, [class*='error'], .woocommerce-error",
    settle_ms=2000,
    fields=_SHOP_FIELDS,
)


class AccountRegistrar:
    """
    Automates account registration on supplier websites.

    Usage:
        from oilwatch.identity import load_contact
        contact = load_contact()
        registrar = AccountRegistrar()
        await registrar.register_all_suppliers(
            contact.name, contact.email, contact.phone, contact.address, contact.postcode
        )
    """

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._results: list[dict[str, Any]] = []

    async def _setup(self) -> Page:
        """Set up browser."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        self._page = await self._context.new_page()
        return self._page

    async def _close(self) -> None:
        """Close browser."""
        if self._page:
            await self._page.context.close()
        if self._playwright:
            await self._playwright.stop()
        self._page = None
        self._browser = None
        self._playwright = None

    async def _accept_cookies(self, page: Page) -> None:
        """Accept cookie consent banners."""
        cookie_selectors = [
            'button:has-text("Accept"), button:has-text("Accept All"), button:has-text("OK")',
            'button[id*="accept"], button[class*="accept"]',
            '.iubenda-cs-accept-btn, #iubenda-cs-accept-btn',
            '[aria-label*="accept"], [title*="accept"]',
        ]

        for selector in cookie_selectors:
            try:
                button = await page.query_selector(selector)
                if button:
                    await button.click(timeout=3000)
                    await page.wait_for_timeout(1000)
                    return
            except Exception as exc:  # noqa: BLE001
                log.debug("cookie selector %r not clickable: %s", selector, exc)

        # Try to close cookie banner
        try:
            close_btn = await page.query_selector('.iubenda-cs-close, button[aria-label*="close"], .cookie-close')
            if close_btn:
                await close_btn.click(timeout=3000)
                await page.wait_for_timeout(1000)
        except Exception as exc:  # noqa: BLE001
            log.debug("cookie close button not clickable: %s", exc)

    async def _fill_fields(self, page: Page, fields: dict[str, str]) -> int:
        """Fill the first matching selector for each field; return how many took.

        Every platform uses a different selector set and most of them will not
        exist on a given page, so a miss is logged and skipped rather than fatal.
        """
        filled = 0
        for selector, value in fields.items():
            if not value:
                continue
            try:
                field = await page.query_selector(selector)
                if field:
                    await field.fill(value)
                    filled += 1
            except Exception as exc:  # noqa: BLE001
                log.debug("could not fill %s: %s", selector, exc)
        return filled

    async def register_scottish_fuels(
        self,
        name: str,
        email: str,
        phone: str,
        address: str,
        postcode: str,
    ) -> dict[str, Any]:
        """Register account on Scottish Fuels website."""
        return await self._register(SCOTTISH_FUELS, name, email)

    async def register_valueoils(
        self,
        name: str,
        email: str,
        phone: str,
        address: str,
        postcode: str,
    ) -> dict[str, Any]:
        """Register account on ValueOils website."""
        return await self._register(VALUEOILS, name, email)

    async def register_homefuels_direct(
        self,
        name: str,
        email: str,
        phone: str,
        address: str,
        postcode: str,
    ) -> dict[str, Any]:
        """Register account on HomeFuels Direct website."""
        return await self._register(HOMEFUELS_DIRECT, name, email)

    async def _register(self, form: RegistrationForm, name: str, email: str) -> dict[str, Any]:
        """Drive one supplier's form and report what the site said.

        The three public methods above keep the signature the CLI and
        :func:`register_all` call with, which is wider than any of the forms ask
        for: none of them takes a phone number, address or postcode.
        """
        password = generate_supplier_password(form.name)
        result: dict[str, Any] = {
            "supplier": form.name,
            "supplier_key": form.key,
            "email": email,
            "password": password,
            "status": "pending",
            "message": "",
            "timestamp": datetime.now().isoformat(),
        }

        try:
            page = await self._setup()
            await page.goto(form.login_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            await self._accept_cookies(page)
            if form.settle_ms:
                await page.wait_for_timeout(form.settle_ms)

            if await page.query_selector("a.logout"):
                result["status"] = "already_registered"
                result["message"] = "Already logged in"
                return result

            await self._follow_register_link(page, form)
            await self._fill_registration_fields(page, form, name, email, password)
            result["status"], result["message"] = await self._submit(page, form)

        except Exception as exc:  # noqa: BLE001
            log.debug("registration on %s raised: %s", form.name, exc)
            result["status"] = "manual_review"
            result["message"] = f"Auto-registration encountered issues. Credentials stored: {password}"
        finally:
            # Whatever happened, the account may exist now, so the password is
            # kept for a manual sign-in, and the browser is closed on every path,
            # including the failure one.
            store_supplier_credentials(form.key, password, form.name, email)
            await self._close()

        return result

    async def _follow_register_link(self, page: Page, form: RegistrationForm) -> None:
        """Follow the "Register" link on the sites that keep the form behind one."""
        if not form.register_link:
            return
        link = await page.query_selector(form.register_link)
        if not link:
            return
        try:
            await link.click()
            await page.wait_for_timeout(2000)
        except Exception as exc:  # noqa: BLE001
            log.debug("register link click failed: %s", exc)

    async def _fill_registration_fields(
        self, page: Page, form: RegistrationForm, name: str, email: str, password: str
    ) -> None:
        values = {
            "first_name": name.split()[0].lower() if name else "",
            "email": email,
            "password": password,
        }
        filled = await self._fill_fields(page, {s: values[k] for s, k in form.fields})
        if not filled and form.fallback_fields:
            await self._fill_fields(page, {s: values[k] for s, k in form.fallback_fields})

    async def _submit(self, page: Page, form: RegistrationForm) -> tuple[str, str]:
        """Click the register button and read the outcome the page gives back."""
        button = await page.query_selector(form.register_button)
        if button is None:
            return "manual_review", "Registration form found. Complete manually with stored credentials."

        try:
            await button.click(timeout=5000)
            await page.wait_for_timeout(5000)
        except Exception as exc:  # noqa: BLE001
            # A successful click often navigates away, which surfaces as an error
            # here; log it so a real failure stays distinguishable.
            log.debug("register click raised (likely navigation): %s", exc)

        error = await page.query_selector(form.error_banner)
        if error is not None:
            return self._read_banner(form, await error.text_content() or "")

        success = None
        if form.success_banner:
            success = await page.query_selector(form.success_banner)
        if success is not None:
            return "registered", "Registration successful. Check email for verification."
        return "manual_review", "Registration submitted. Check email for verification."

    @staticmethod
    def _read_banner(form: RegistrationForm, text: str) -> tuple[str, str]:
        """Read the outcome out of the banner the page left behind."""
        lowered = text.lower()
        if "exists" in lowered:
            return "already_registered", "Account already exists"
        if form.error_says_success and ("created" in lowered or "success" in lowered):
            return "registered", "Registration successful. Check email for verification."
        if not text:
            # An empty banner says nothing either way. Calling it a review keeps
            # the owner's eye on it instead of leaving the status at pending.
            return "manual_review", "The form reported an error with no message. Credentials stored."
        return "manual_review", f"Form issue: {text[:100]}. Credentials stored."

    async def register_all_suppliers(
        self,
        name: str,
        email: str,
        phone: str,
        address: str,
        postcode: str,
    ) -> list[dict[str, Any]]:
        """Register accounts on all supplier websites."""
        forms = (
            (SCOTTISH_FUELS.name, self.register_scottish_fuels),
            (VALUEOILS.name, self.register_valueoils),
            (HOMEFUELS_DIRECT.name, self.register_homefuels_direct),
        )

        self._results = []
        for label, flow in forms:
            print(f"Registering on {label}...")
            result = await flow(name, email, phone, address, postcode)
            self._results.append(result)
            print(f"  Status: {result['status']} - {result['message']}")

        return self._results

    def save_results(
        self, output_path: Path | str, results: list[dict[str, Any]] | None = None
    ) -> Path:
        """Save registration results to JSON file.

        Pass ``results`` to write a run's results without having pushed them
        through this instance first; the default is the last run's.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._results if results is None else results
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return output_path


async def register_all(
    name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    address: str | None = None,
    postcode: str | None = None,
    headless: bool = True,
) -> list[dict[str, Any]]:
    """
    Convenience function to register on all supplier websites.

    Args:
        name: Full name for registration (defaults to the configured contact)
        email: Email address (defaults to the configured contact email)
        phone: Phone number (defaults to the configured contact phone)
        address: Passed through for the caller's sake; the supplier registration
            forms ask for a name, an email and a password and nothing else
        postcode: Postcode (defaults to the configured delivery postcode)
        headless: Run browser in headless mode

    Returns:
        List of registration results
    """
    contact = load_contact()
    registrar = AccountRegistrar(headless=headless)
    return await registrar.register_all_suppliers(
        name or contact.name,
        email or contact.email,
        phone if phone is not None else contact.phone,
        address or "",
        postcode or contact.postcode,
    )
