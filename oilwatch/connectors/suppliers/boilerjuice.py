"""BoilerJuice browser connector with auto-login and quote extraction."""

from __future__ import annotations

import re
from typing import Any

from oilwatch.connectors.browser_base import BrowserConnector
from oilwatch.connectors.protocols import PageLike
from oilwatch.identity import load_contact
from oilwatch.logging_setup import get_logger
from oilwatch.models import QuoteResult
from oilwatch.pricing import inclusive_price_and_total, normalise_price_per_litre

log = get_logger("connectors.boilerjuice")

#: The standard delivery option's inclusive total ("You Pay"). BoilerJuice tags
#: each option in the markup (``price_standard_value``, ``price_delivery5_value``
#: …) and the faster options carry their own, higher totals, so the standard one
#: is read by its tag rather than taking the cheapest of the "You Pay" amounts.
STANDARD_TOTAL_RE = re.compile(
    r'data-test="price_standard_value"[^>]*>\s*(?:£|&pound;)\s*([\d,]+\.\d{2})'
)

#: The quote form's postcode control, shared by the readiness wait and the fill.
_POSTCODE_SELECTOR = (
    'input[name="postcode"], input[id*="postcode"], '
    'input[placeholder*="postcode"], input[autocomplete="postal-code"]'
)


class BoilerJuiceBrowserConnector(BrowserConnector):
    """
    BoilerJuice browser connector.
    
    BoilerJuice has an online quote system that requires login.
    Quote URL: https://www.boilerjuice.com/uk/journeys/core/quote
    
    This connector:
    1. Logs in with stored credentials
    2. Navigates to quote page
    3. Fills in postcode and quantity
    4. Extracts price from quote response
    """

    #: Markers the page shows only to a signed-in account, used to tell a
    #: successful sign-in from a form that bounced back. BoilerJuice's signed-in
    #: header offers a sign-out link; the generic ``a.logout`` names it never
    #: used, so a real sign-in looked like a failure.
    SIGNED_IN_SELECTORS = "a[href*='logout'], a[href*='sign_out'], a:has-text('Sign out')"

    #: Cookiebot's consent dialog covers the page; the sign-in form is not
    #: reachable until it is answered.
    CONSENT_SELECTORS = (
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
        "button:has-text('Allow all')",
    )

    def __init__(self) -> None:
        super().__init__()
        self.supplier_key = "boilerjuice"
        self.supplier_name = "BoilerJuice"
        self.base_url = "https://www.boilerjuice.com"
        # The real sign-in path. "/uk/login" serves a 404 page that still
        # renders the site chrome, so the form was never found and every run
        # reported the bare "Login failed: .".
        self.login_url = "https://www.boilerjuice.com/uk/users/login"
        self.quote_url = "https://www.boilerjuice.com/uk/journeys/core/quote"

    async def login(self, page: PageLike, email: str, password: str) -> bool:
        """
        Log in to BoilerJuice.

        Returns True when the page shows a signed-in account. Raises
        ``RuntimeError`` describing what the page showed when the sign-in form
        is missing or the credentials are refused, so ``BrowserConnector`` can
        report that reason instead of the bare "Login failed: ." a silent
        ``False`` produced.
        """
        try:
            await page.goto(self.login_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            await self._accept_cookie_consent(page)

            # Check if already logged in
            if await page.query_selector(self.SIGNED_IN_SELECTORS):
                return True

            # Find and fill login form. The submit is pinned by id: a bare
            # ``button[type="submit"]`` matches Cookiebot's dialog buttons first.
            email_field = await page.query_selector('input[name="email"], input[type="email"], input[id*="email"]')
            password_field = await page.query_selector('input[name="password"], input[type="password"], input[id*="password"]')
            login_button = await page.query_selector('#login-btn, button:has-text("Login"), input[value*="Login"]')

            if not (email_field and password_field and login_button):
                # Say what the page held: "sign-in form not found" against a real
                # page is only actionable once the markup it actually served is
                # visible, not by guessing another selector list.
                try:
                    seen = (await page.content())[:1500]
                except Exception as exc:  # noqa: BLE001
                    seen = f"<page content unavailable: {exc}>"
                log.warning(
                    "BoilerJuice sign-in form not found at %s; page (%s):\n%s",
                    page.url,
                    "captured" if seen else "empty",
                    seen,
                )
                raise RuntimeError(
                    "the sign-in form was not found (no email, password or submit control)"
                )

            await email_field.fill(email)
            await password_field.fill(password)
            await login_button.click()

            # Wait for navigation
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception as exc:  # noqa: BLE001
                log.debug("login page did not reach networkidle: %s", exc)

            await page.wait_for_timeout(3000)

            # Check if login was successful
            if await page.query_selector(self.SIGNED_IN_SELECTORS):
                return True

            # Say what the page showed rather than returning a reasonless False.
            error = await page.query_selector(".error, .alert-danger, .validation-error")
            if error:
                error_text = (await error.text_content() or "").strip()
                raise RuntimeError(f"the sign-in form reported an error: {error_text}")

            raise RuntimeError("no signed-in account marker appeared after submitting the sign-in form")

        except RuntimeError:
            raise
        except Exception as e:
            log.debug("login error: %s", e)
            raise RuntimeError(f"the sign-in attempt raised {type(e).__name__}: {e}") from e

    async def _accept_cookie_consent(self, page: PageLike) -> None:
        """Dismiss Cookiebot's consent dialog, best-effort.

        The dialog covers the page and the sign-in form is not reachable behind
        it, so this runs before the form is looked for. Absence is fine: a
        returning visitor's consent is already recorded.
        """
        for selector in self.CONSENT_SELECTORS:
            try:
                button = await page.query_selector(selector)
            except Exception as exc:  # noqa: BLE001
                log.debug("cookie consent lookup failed for %r: %s", selector, exc)
                continue
            if not button:
                continue
            try:
                await button.click()
                await page.wait_for_timeout(2500)
                return
            except Exception as exc:  # noqa: BLE001
                log.debug("cookie consent click failed for %r: %s", selector, exc)

    async def get_quote_with_browser(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
        page: PageLike,
    ) -> QuoteResult:
        """
        Get a quote from BoilerJuice using browser automation.
        
        Strategy:
        1. Navigate to quote page
        2. Fill in postcode and quantity
        3. Submit and extract price
        """
        try:
            postcode = context.get("postcode", "") or load_contact().postcode
            
            # Navigate to quote page
            await page.goto(self.quote_url, wait_until="domcontentloaded")
            # Wait for the quote page rather than sleeping a flat 3s: the form
            # control (or a quote already rendered for a signed-in account) is
            # the readiness signal, and the wait is bounded.
            await self._wait_for(
                page,
                lambda: self._quote_page_ready(page),
                what="the BoilerJuice quote page",
            )

            # Find and fill quote form fields
            # Postcode field
            postcode_field = await page.query_selector(_POSTCODE_SELECTOR)
            
            # Quantity field (usually a dropdown or number input)
            quantity_field = await page.query_selector(
                'input[name="quantity"], input[id*="quantity"], '
                'input[name="litres"], input[id*="litres"], '
                'input[type="number"], select[name="quantity"]'
            )
            
            # Get quote button. Pinned by id: a bare ``button:has-text("Price")``
            # matched the hidden "Price Charts" nav button first, so the click
            # waited 30s on an element that is never visible.
            quote_button = await page.query_selector(
                '#get-quote-button, button:has-text("Get Quote"), button:has-text("Quote")'
            )
            
            # The form is driven best-effort. This page keeps its "buy now" form
            # inside a collapsed accordion (``#collapseUpdateQuote``) and already
            # renders the quote for a signed-in account, so a field that will not
            # take a value must not turn the whole run into a browser error. A
            # short timeout keeps a hidden field from costing the full default.
            try:
                if postcode_field:
                    await postcode_field.fill(postcode, timeout=5000)
            except Exception as exc:  # noqa: BLE001
                log.debug("could not fill postcode: %s", exc)

            if quantity_field:
                try:
                    tag = await quantity_field.evaluate("el => el.tagName")
                    if tag.upper() == "SELECT":
                        # Dropdown - select closest option
                        options = await quantity_field.query_selector_all("option")
                        for option in options:
                            value = await option.get_attribute("value")
                            if value and str(quantity_liters) in value:
                                await quantity_field.select_option(value)
                                break
                    else:
                        await quantity_field.fill(str(quantity_liters), timeout=5000)
                except Exception as exc:  # noqa: BLE001
                    log.debug("could not set quantity: %s", exc)

            # Submit form
            if quote_button:
                try:
                    await quote_button.click(timeout=5000)

                    # Wait for the price to render rather than sleeping a flat
                    # 5s; the extraction below re-reads it once it is there.
                    await self._wait_for(
                        page,
                        lambda: self._quote_ready(page, quantity_liters),
                        what="a BoilerJuice price",
                    )
                except Exception as exc:  # noqa: BLE001
                    log.debug("could not submit the quote form: %s", exc)
            
            # Read the price from the inclusive "You Pay" total, which carries the
            # service charge the headline ppl omits (see the supplier note). The
            # fallback keeps the ppl path, but reports its inc-VAT basis like every
            # other connector, so BoilerJuice is not compared on a cheaper number.
            try:
                content = await page.content()
            except Exception as exc:  # noqa: BLE001 - a detached page is "no price"
                log.debug("could not read the quote page: %s", exc)
                content = ""
            inclusive_total = self.parse_inclusive_total(content)
            if inclusive_total is not None:
                price_per_liter = round(inclusive_total / quantity_liters, 4)
                notes = (
                    f"Price from BoilerJuice's quote options for {quantity_liters}L "
                    f"(£{inclusive_total:.2f} inc-VAT, incl. the service charge)."
                )
                raw_payload = {
                    "quote_url": self.quote_url,
                    "postcode": postcode,
                    "method": "browser_automation",
                    "inclusive_total": inclusive_total,
                }
            else:
                ex_vat = await self._extract_price(page, quantity_liters)
                if ex_vat is None:
                    # No price found - return manual action required
                    return QuoteResult(
                        supplier_id=int(supplier["id"]),
                        supplier_name=supplier["name"],
                        observed_at=self.now(),
                        quantity_liters=quantity_liters,
                        status="manual_action_required",
                        reason="no_price_found",
                        source="boilerjuice_browser",
                        notes="Logged in but could not extract automated price. Please complete quote manually at: https://www.boilerjuice.com/uk/journeys/core/quote",
                    )
                price_per_liter, inclusive_total = inclusive_price_and_total(ex_vat, quantity_liters)
                notes = (
                    f"Price extracted via browser automation for {quantity_liters}L. "
                    f"Ex VAT: £{ex_vat * quantity_liters:.2f}, Inc VAT: £{inclusive_total:.2f}"
                )
                raw_payload = {
                    "quote_url": self.quote_url,
                    "postcode": postcode,
                    "method": "browser_automation",
                }

            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="ok",
                price_per_liter=price_per_liter,
                total_price=inclusive_total,
                source="boilerjuice_browser",
                notes=notes,
                raw_payload=raw_payload,
            )
            
        except Exception as e:  # noqa: BLE001 - every browser fault becomes an error quote
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="error",
                reason="site_error",
                source="boilerjuice_browser",
                notes=f"Browser automation error: {e!s}",
            )
    
    @staticmethod
    def parse_inclusive_total(content: str) -> float | None:
        """The Standard Delivery option's inclusive total ("You Pay").

        BoilerJuice is a broker: the headline "ppl" is ex-VAT and omits its
        service charge, so the quote is read from a total (see the supplier
        note). The standard option's total is the comparable one — each faster
        option adds its own surcharge — so it is read by its tag rather than
        taking the minimum across options. ``None`` when it is not on the page.
        """
        match = STANDARD_TOTAL_RE.search(content)
        if not match:
            return None
        return float(match.group(1).replace(",", ""))

    async def _quote_page_ready(self, page: PageLike) -> bool:
        """True once the quote form, or a rendered quote, is on the page."""
        if await page.query_selector(_POSTCODE_SELECTOR) is not None:
            return True
        try:
            return self.parse_inclusive_total(await page.content()) is not None
        except Exception:  # noqa: BLE001 - an unreadable page is simply not ready
            return False

    async def _quote_ready(self, page: PageLike, quantity_liters: int) -> bool:
        """True once a price can be read off the page, for the bounded wait."""
        try:
            content = await page.content()
        except Exception:  # noqa: BLE001 - an unreadable page is simply not ready
            return False
        if self.parse_inclusive_total(content) is not None:
            return True
        return await self._extract_price(page, quantity_liters) is not None

    async def _extract_price(self, page: PageLike, quantity_liters: int) -> float | None:
        """Extract price per litre from the page."""
        try:
            # Get page content
            content = await page.content()
            
            # Look for price patterns
            patterns = [
                r'£?(\d+\.\d{2})\s*(?:per\s*)?litre',
                r'£?(\d+\.\d{2})\s*p(?:ence)?/l',
                r'price.*?£?(\d+\.\d{2})',
                r'total.*?£?(\d+\.\d{2})',
                r'(\d+)p\s*/\s*l',
                r'unit\s*price.*?£?(\d+\.\d{2})',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    price = normalise_price_per_litre(match.group(1))
                    if price is not None:
                        return price

            # Try to find price in specific elements
            price_elements = await page.query_selector_all(
                '.price, .total, .quote-price, [class*="price"], [id*="price"], '
                '.order-summary [class*="amount"]'
            )

            for element in price_elements:
                text = await element.text_content()
                if text:
                    match = re.search(r'(\d+\.\d{2})', text)
                    if match:
                        price = normalise_price_per_litre(match.group(1))
                        if price is not None:
                            return price

            return None
            
        except Exception as e:  # noqa: BLE001 - an unreadable page is simply "no price"
            log.debug("price extraction failed: %s", e)
            return None
    
