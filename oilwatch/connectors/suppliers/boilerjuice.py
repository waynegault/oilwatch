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


#: Read back the journey's own controls: what each holds, the options the two
#: selects offer, and whether the browser would actually submit them. Values are
#: read as properties because a value a script set is not in the serialised HTML,
#: so a page dump cannot say whether a fill took. ``checkValidity`` is the part
#: that matters: this form submits *nothing at all* when a required control is
#: unset, and its two selects open on placeholder options.
_FORM_STATE_SCRIPT = (
    "() => Array.from(document.querySelectorAll("
    "\"select[name='oil_type'], select[name='theTanker'], "
    "input[name='postcode'], input[name='volume'], input[name='email']\"))"
    ".map(el => {"
    "const tag = el.tagName.toLowerCase();"
    "const flags = (el.required ? ' required' : '')"
    "+ (el.willValidate && !el.checkValidity() ? ' INVALID' : '');"
    "const detail = tag === 'select'"
    "? 'options=[' + Array.from(el.options).map(o => o.value + '|' + o.text).join('; ')"
    "+ '] value=' + JSON.stringify(el.value)"
    ": 'value=' + JSON.stringify(el.value);"
    "return tag + '[' + el.name + '] ' + detail + flags;"
    "}).join('; ')"
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

    #: The domestic/commercial chooser the journey shows over its form. Until it
    #: is answered the page is covered and a click on the form's own submit is
    #: swallowed by the overlay, which is why the run reported no price: the quote
    #: was never asked for, and the click failure that said so was logged at debug
    #: level where nobody sees it.
    USAGE_POPUP_SELECTORS = (
        "#usage-modal-domestic",
        "button:has-text('Continue as domestic')",
    )

    #: Cookiebot's consent dialog covers the page; the sign-in form is not
    #: reachable until it is answered.
    CONSENT_SELECTORS = (
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
        "button:has-text('Allow all')",
    )

    #: The journey's form will not submit until both of these are answered: they
    #: open on placeholder options (an empty value labelled "Oil Type" and
    #: "Tanker Type"), and the browser refuses a required control left unset - so
    #: the page sat there, unchanged, and the run reported no price. The values are
    #: the page's own: plain kerosene heating oil, and the standard tanker whose
    #: delivery total this connector reads in preference to the faster options.
    OIL_TYPE_SELECTOR = "select[name='oil_type']"
    OIL_TYPE_VALUE = "Heating Oil (Kerosene28)"
    TANKER_SELECTOR = "select[name='theTanker']"
    TANKER_VALUE = "Standard Tanker"

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

    async def _select_option(self, page: PageLike, selector: str, value: str, what: str) -> None:
        """Choose an option, saying so when it cannot be chosen.

        A select left on its placeholder is not a cosmetic miss here: the form
        will not submit while one is unanswered, and a submit that does not happen
        leaves a page indistinguishable from one that carries no quote.
        """
        try:
            element = await page.query_selector(selector)
        except Exception as exc:  # noqa: BLE001 - an unreadable page is reported, not raised
            log.warning("BoilerJuice: could not look for the %s control: %s", what, exc)
            return
        if element is None:
            log.warning("BoilerJuice: no %s control on the quote page (%s)", what, selector)
            return
        try:
            await element.select_option(value)
        except Exception as exc:  # noqa: BLE001 - reported rather than swallowed
            log.warning("BoilerJuice: could not set the %s to %r: %s", what, value, exc)

    async def _confirm_domestic_usage(self, page: PageLike) -> None:
        """Answer the domestic/commercial chooser, best-effort.

        This install is a home address buying domestic heating oil, which is what
        the register and the contact details describe, so domestic is the answer.
        Absence is fine: a returning visitor's choice is already recorded.
        """
        for selector in self.USAGE_POPUP_SELECTORS:
            try:
                button = await page.query_selector(selector)
            except Exception as exc:  # noqa: BLE001
                log.debug("usage chooser lookup failed for %r: %s", selector, exc)
                continue
            if not button:
                continue
            try:
                await button.click()
                await page.wait_for_timeout(2000)
                return
            except Exception as exc:  # noqa: BLE001
                log.debug("usage chooser click failed for %r: %s", selector, exc)

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
            # Answer the chooser before touching the form: while it is showing it
            # covers the page, and its overlay swallows the form's own submit.
            await self._confirm_domestic_usage(page)

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

            # Answer the two required selects, without which the form submits
            # nothing at all - see the constants for what they are and why. Only
            # when there is a Get Quote control: a page without one has no form to
            # complete, and the report below says exactly that rather than this
            # complaining about a control such a page was never going to carry.
            if quote_button:
                await self._select_option(page, self.OIL_TYPE_SELECTOR, self.OIL_TYPE_VALUE, "oil type")
                await self._select_option(page, self.TANKER_SELECTOR, self.TANKER_VALUE, "tanker type")

            # Submit form. The attempt reports what it did, rather than only
            # logging at debug: a click that never lands leaves a page that looks
            # exactly like one with no quote on it, and telling those apart is the
            # difference between a fix and another guess (see the report below).
            submit_note = "no Get Quote control matched the page"
            if quote_button:
                try:
                    await quote_button.click(timeout=5000)

                    # Wait for the price to render rather than sleeping a flat
                    # 5s; the extraction below re-reads it once it is there.
                    submit_note = "clicked; no price appeared within the wait"
                    await self._wait_for(
                        page,
                        lambda: self._quote_ready(page, quantity_liters),
                        what="a BoilerJuice price",
                    )
                    submit_note = "clicked"
                except Exception as exc:  # noqa: BLE001
                    submit_note = f"the click raised {type(exc).__name__}: {exc}"
            
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
                    # Say what the page held. This path returned a bare reason
                    # before, so a miss could not be told apart from a markup
                    # change without another live run - the fault the sign-in
                    # path above already corrected for itself.
                    log.warning(
                        "BoilerJuice quote page carried no readable price at %s; "
                        "submission: %s; page (%s, %d chars):\n%s\ncontrols: %s\nform: %s",
                        page.url,
                        submit_note,
                        "captured" if content else "empty",
                        len(content),
                        self._price_context(content),
                        await self._control_inventory(page),
                        await self._form_state(page),
                    )
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
                    f"Price from BoilerJuice's per-litre figure for {quantity_liters}L "
                    f"(Ex VAT: £{ex_vat * quantity_liters:.2f}, Inc VAT: £{inclusive_total:.2f}). "
                    "BoilerJuice is a broker whose service charge shows up in its option "
                    "totals rather than in this figure, so treat this as the cheaper "
                    "number to compare on until the option totals are read again."
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

    @staticmethod
    async def _form_state(page: PageLike) -> str:
        """What the journey's form holds, and whether it would submit at all."""
        try:
            return await page.evaluate(_FORM_STATE_SCRIPT)
        except Exception as exc:  # noqa: BLE001 - an unreadable page is not a crash
            return f"<form state unavailable: {exc}>"

    @staticmethod
    async def _control_inventory(page: PageLike, shown: int = 30) -> str:
        """What the page offers to type into or click, for diagnosing a miss.

        Every control this flow needs is looked for by selector: the postcode and
        quantity fields, and the button that submits the journey. A selector that
        no longer matches leaves the page sitting on its own unquoted form - which
        reads as "no price found" and nothing else - so naming what is actually
        there is what turns that into a fix rather than another guess.
        """
        try:
            controls = await page.query_selector_all("input, select, button")
        except Exception as exc:  # noqa: BLE001 - an unreadable page is not a crash
            return f"<controls unavailable: {exc}>"
        described = []
        for control in controls[:shown]:
            try:
                tag = str(await control.evaluate("el => el.tagName")).lower()
                name = await control.get_attribute("name")
                ident = await control.get_attribute("id")
                kind = await control.get_attribute("type")
                label = (await control.text_content() or "").strip()[:40]
            except Exception:  # noqa: BLE001 - a detached control says nothing
                continue
            described.append(f"{tag}[name={name} id={ident} type={kind}]{f' {label!r}' if label else ''}")
        return f"{len(controls)} found, first {len(described)}: " + ", ".join(described)

    @staticmethod
    def _price_context(content: str, limit: int = 1200) -> str:
        """The markup around the first marker that could actually be a price.

        A whole-page dump would be the ``<head>`` and nothing else, so this opens
        a window before the marker instead. They are tried in order of how much
        they say, because the bare word "price" matches the nav's "Price Charts"
        link first on the real page - a window that shows nothing about the quote.
        """
        for pattern in (r'data-test="price', r"£|&pound;", r"You Pay", r"price"):
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                start = max(0, match.start() - limit // 3)
                return content[start : start + limit]
        return content[:limit]

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
            
            # Every pattern is anchored to a per-litre unit or to a two-word
            # label. There used to be a bare `price.*?£?(\d+\.\d{2})` and a
            # `total.*?…` beside it, and those match the *delivery total*: on
            # `£1,195.70` the comma is simply skipped, `\d+` starts at "195", and
            # the capture normalises to £1.957/L and is then lifted 5% — a wrong
            # price in the database, which the register's own note says this
            # connector must not produce ("the inclusive 'You Pay' option totals
            # … are no longer matched"). No price beats a fabricated one.
            patterns = [
                r'£?(\d+\.\d{2})\s*(?:per\s*)?litre',
                r'£?(\d+\.\d{2})\s*p(?:ence)?/l',
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
    
