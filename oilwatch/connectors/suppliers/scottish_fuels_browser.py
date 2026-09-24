"""Scottish Fuels connector (undetected browser + persisted session).

Scottish Fuels' quote system is a Magento site whose login is protected by
reCAPTCHA v3, which a plain automated browser cannot pass. Instead this
connector reuses the session established once by ``oilwatch login
scottish_fuels`` (see ``oilwatch.browser_auth.BrowserAuth``): the user signs in
by hand once, and the persistent Chrome profile keeps the session for later
runs.

The quote flow (reverse-engineered live) is:

1. Open ``/quote/`` (the "QUOTE DETAILS" form).
2. Select the fuel type radio ``productSelection`` (451 = Premium Kerosene,
   418 = Heating Oil) and enter a quantity.
3. Click "Get Quote", which POSTs ``/rest/V2/customer/login/quote`` with
   ``{"product": {"product_sku": "451", "quantity": 1000, "postcode": ...}}``.
4. The fresh quote renders server-side, e.g. ``101.03p (Excl. VAT)``.

This connector reads that fresh quote and returns the price per litre
(ex-VAT pence) normalised to inclusive of 5% VAT.

**Session expiry is the failure mode to expect.** When there is no live
customer session, ``/quote/`` answers 302 to ``/customer/account/``. The page
then looks "changed" — the fuel-type radios simply are not there — so this
connector checks the redirect *before* touching the form and reports the real
cause instead of a NoSuchElementException.
"""

from __future__ import annotations

import re
import time
from typing import Any

from oilwatch.connectors.base import BaseConnector
from oilwatch.credentials import get_supplier_credentials
from oilwatch.identity import load_contact
from oilwatch.logging_setup import get_logger
from oilwatch.models import QuoteResult
from oilwatch.pricing import inclusive_price_and_total, pence_to_pounds
from oilwatch.waiting import wait_until

log = get_logger("connectors.scottish_fuels")

# Substrings that mean "we were bounced to the sign-in screen". Deliberately
# narrow: the signed-in account dashboard also lives under /customer/account/,
# so a bare "/customer/account" test would call a working session a failure.
LOGIN_PAGE_MARKERS = ("/customer/account/login", "/login")

# Labels that identify a kerosene-type product when the configured SKU is gone.
KEROSENE_LABELS = ("kerosene", "heating oil", "premium")

#: A rendered quote line, e.g.
#: ``Premium Kerosene 1000 116.99p (Excl. VAT) £1169.90 £58.50 £1228.40`` —
#: quantity, ex-VAT pence per litre, then ex-VAT / VAT / inc-VAT totals.
QUOTE_ROW_RE = re.compile(
    r"(\d+)\s+(\d+(?:\.\d{1,2})?)p\s*\(Excl\.?\s*VAT\)\s+"
    r"£([\d,]+\.\d{2})\s+£([\d,]+\.\d{2})\s+£([\d,]+\.\d{2})",
    re.IGNORECASE,
)


class ScottishFuelsBrowserConnector(BaseConnector):
    quote_url = "https://quote.scottishfuels.co.uk/quote/"
    login_url = "https://quote.scottishfuels.co.uk/customer/account/login/"
    product_sku = "451"  # Premium Kerosene

    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        postcode = context.get("postcode", "") or load_contact().postcode
        config = supplier.get("connector_config") or {}
        configured_sku = config.get("product_sku", self.product_sku)

        try:
            from selenium.webdriver.common.by import By

            from oilwatch.browser_auth import BrowserAuth

            auth = BrowserAuth("scottish_fuels")
            # Headful on purpose. reCAPTCHA v3 scores a headless browser far
            # lower, which is what made the credential sign-in fail. Ancestry
            # runs headful by default (HEADLESS_MODE=false) for the same reason.
            driver = auth.launch(headless=False)
            try:
                driver.get(self.quote_url)
                self._wait_for_quote_form(driver)

                if self.is_login_page(driver.current_url):
                    # The session cookie lasts only ~15 minutes, so an expired
                    # session is routine rather than exceptional: sign in again
                    # with the stored credentials instead of sending the owner
                    # off to run the login command by hand.
                    creds = get_supplier_credentials("scottish_fuels") or {}
                    email = creds.get("email") or load_contact().email
                    password = creds.get("password") or ""
                    if not (email and password):
                        return self._manual(
                            supplier,
                            quantity_liters,
                            "Not signed in and no stored credentials for "
                            "scottish_fuels. Run `oilwatch register` first.",
                            # The portal is fine and the stored session is not;
                            # only a sign-in fixes it. Same reading as the
                            # `is_logged_in` case below.
                            reason="login_not_confirmed",
                        )
                    log.info("Scottish Fuels session expired; signing in again")
                    try:
                        signed_in = auth.sign_in(driver, self.login_url, email, password)
                    except Exception as exc:  # noqa: BLE001
                        return self._manual(
                            supplier,
                            quantity_liters,
                            f"Session expired and automatic sign-in failed: {exc}. "
                            "Run `oilwatch login scottish_fuels` by hand.",
                            # The sign-in *raised* — a timeout or a broken page —
                            # which is a fault rather than a refused session.
                            reason="site_error",
                        )
                    if not signed_in:
                        return self._manual(
                            supplier,
                            quantity_liters,
                            "Session expired and the automatic sign-in did not take "
                            "after retrying. Run `oilwatch login scottish_fuels` by "
                            "hand.",
                            reason="login_not_confirmed",
                        )
                    # Refresh the saved cookie backup too. The persistent profile
                    # is what carries the session — the file is a backup nothing
                    # reads back — so a failure here is not worth more than the
                    # debug line below, and the profile still holds the session.
                    try:
                        auth.save_cookies()
                    except Exception as exc:  # noqa: BLE001 - the profile still holds the session
                        log.debug("could not refresh the saved cookies after re-login: %s", exc)
                    driver.get(self.quote_url)
                    self._wait_for_quote_form(driver)
                    if self.is_login_page(driver.current_url):
                        return self._manual(
                            supplier,
                            quantity_liters,
                            "Signed in but /quote/ still redirects to the account page.",
                            reason="login_not_confirmed",
                        )

                # select fuel type, tolerating a changed/renumbered option list
                options = self.product_options(driver)
                chosen_sku = self.choose_product_sku(configured_sku, options)
                if chosen_sku is None:
                    return self._manual(
                        supplier,
                        quantity_liters,
                        "No fuel-type options on the quote form (expected a "
                        f"productSelection radio; saw {options or 'none'}). "
                        "The form may have been redesigned.",
                        # The page answered and the control it needs was not on
                        # it, which is a page to look at rather than a session.
                        reason="no_price_found",
                    )
                driver.execute_script(
                    "arguments[0].click();",
                    driver.find_element(
                        By.CSS_SELECTOR, f"input[name='productSelection'][value='{chosen_sku}']"
                    ),
                )
                time.sleep(0.5)

                # quantity. The control arrives pre-filled with the order size,
                # and the page's own validation rewrites an emptied one to its
                # 500 L minimum — so it is never cleared. Clearing it silently
                # turned every 1000 L quote into a 500 L one, which was then
                # still reported, and priced, as 1000 L.
                #
                # What it holds *afterwards* is kept, not just typed: the page
                # clamps to its own min/max/step, so this is what the site will
                # quote, and the fallback below prices against it rather than
                # against the number we asked for.
                typed_quantity = self.set_quantity(driver, quantity_liters)
                time.sleep(0.5)

                # Get Quote
                button = driver.find_element(By.XPATH, "//button[contains(., 'Get Quote')]")
                driver.execute_script("arguments[0].click();", button)
                # Wait for the fresh quote (or a mid-quote redirect) rather than
                # a flat 15s: the result is on the page well before that.
                self._wait_for_quote_result(driver)

                body_text = driver.find_element(By.TAG_NAME, "body").text
                final_url = driver.current_url
            finally:
                auth.close()
        except Exception as exc:  # noqa: BLE001
            return self._manual(
                supplier,
                quantity_liters,
                f"Browser automation error: {exc}",
                reason="site_error",
                status="error",
            )

        if final_url and self.is_login_page(final_url):
            return self._manual(
                supplier,
                quantity_liters,
                "Session expired mid-quote (redirected back to "
                "/customer/account/). Run `oilwatch login scottish_fuels` and retry.",
                reason="login_not_confirmed",
            )

        if not self.is_logged_in(body_text):
            return self._manual(
                supplier,
                quantity_liters,
                "Not signed in. Run `oilwatch login scottish_fuels` once to establish a session.",
                # Distinct from a broken connector: the portal is fine, the
                # stored session is not, and only a sign-in fixes it.
                reason="login_not_confirmed",
            )

        quoted = self.parse_quote_row(body_text)
        ex_vat_price = (quoted or {}).get("price_ex_vat") or self.parse_ppl(body_text)
        if ex_vat_price is None:
            # Say what the page actually held. A parse that quietly reports "no
            # price" is indistinguishable from a page that has none, and the
            # pattern can only be realigned against the real text.
            log.warning(
                "no price recognised on the Scottish Fuels result page (%s); text follows:\n%s",
                final_url or self.quote_url,
                body_text[:2000],
            )
            return self._manual(
                supplier,
                quantity_liters,
                "Could not find a price on the quote result page.",
                reason="no_price_found",
            )

        # What the site states, not what we multiply out: its own inclusive
        # total for the quantity it quoted, with the per-litre rate derived from
        # that so the two can never disagree. Falling back to `typed_quantity`
        # before the asked-for figure: when the full quote row could not be
        # parsed, the control still says what the site was asked to price, and
        # the page clamps it.
        quoted_quantity = int(
            (quoted or {}).get("quantity") or typed_quantity or quantity_liters
        )
        if quoted and quoted["inc_vat_total"] > 0:
            total_price = quoted["inc_vat_total"]
            price_per_liter = round(total_price / quoted_quantity, 4)
        else:
            price_per_liter, total_price = inclusive_price_and_total(ex_vat_price, quoted_quantity)
        sku_note = "" if chosen_sku == configured_sku else f" (configured {configured_sku}, used {chosen_sku})"
        basis = f"{quoted_quantity}L"
        if quoted_quantity != quantity_liters:
            basis += f" (asked for {quantity_liters}L; the site quoted {quoted_quantity}L)"
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quoted_quantity,
            status="ok",
            price_per_liter=price_per_liter,
            total_price=total_price,
            source="scottish_fuels_browser",
            notes=(
                f"Fresh quote from Scottish Fuels for {basis} "
                f"(ex-VAT £{ex_vat_price:.4f}/L, inc-VAT £{price_per_liter:.4f}/L; "
                f"site total £{total_price:.2f} inc VAT). "
                f"Postcode: {postcode}. Product SKU: {chosen_sku}{sku_note}."
            ),
            raw_payload={
                "quote_url": self.quote_url,
                "postcode": postcode,
                "price_ex_vat": ex_vat_price,
                "quoted_quantity": quoted_quantity,
                "inc_vat_total": total_price,
                "product_sku": chosen_sku,
                "configured_product_sku": configured_sku,
                "product_options": options,
            },
        )

    @staticmethod
    def is_login_page(url: str | None) -> bool:
        """True when the quote page bounced us to the customer account screen.

        ``/quote/`` answers 302 to ``/customer/account/`` with no live session,
        which is what makes the fuel-type radios appear to have vanished.
        """
        lowered = (url or "").lower()
        return any(marker in lowered for marker in LOGIN_PAGE_MARKERS)

    @staticmethod
    def choose_product_sku(configured_sku: str, options: dict[str, str]) -> str | None:
        """Choose which fuel-type SKU to select. Pure, so it can be unit tested.

        ``options`` maps radio ``value`` -> human label. The supplier renumbers
        these over time, so an unknown configured SKU falls back to the first
        option that looks like kerosene rather than failing outright.
        """
        if not options:
            return None
        if configured_sku in options:
            return configured_sku
        for value, label in options.items():
            if any(word in label.lower() for word in KEROSENE_LABELS):
                return value
        return next(iter(options))

    @staticmethod
    def product_options(driver) -> dict[str, str]:
        """Map each ``productSelection`` radio value to its visible label."""
        options: dict[str, str] = {}
        radios = driver.find_elements("css selector", "input[name='productSelection']")
        for radio in radios:
            value = radio.get_attribute("value")
            if not value:
                continue
            options[value] = ScottishFuelsBrowserConnector._option_label(driver, radio)
        return options

    @staticmethod
    def _option_label(driver, radio) -> str:
        """Best-effort visible label for a radio (``<label for>`` or wrapper)."""
        script = """
        const e = arguments[0];
        const byFor = e.id ? document.querySelector("label[for='" + e.id + "']") : null;
        const label = e.closest('label') || byFor;
        return (label ? label.textContent : (e.parentElement ? e.parentElement.textContent : '')) || '';
        """
        try:
            return (driver.execute_script(script, radio) or "").strip()
        except Exception:  # noqa: BLE001 - a missing label must not break the quote
            return ""

    @staticmethod
    def is_logged_in(body_text: str) -> bool:
        lowered = body_text.lower()
        return "logout" in lowered or "welcome" in lowered or "your quote" in lowered or "quote details" in lowered

    @staticmethod
    def parse_ppl(text: str) -> float | None:
        """Extract the ex-VAT price-per-litre (pence) from the quote page text.

        Matches either "101.03p per litre (Excl. VAT)" or the results-table
        form "101.03p (Excl. VAT)". Returns the cheapest if several appear.
        """
        values = []
        for m in re.finditer(r"(\d+(?:\.\d{1,2})?)p(?:\s*per\s*litre)?\s*\(Excl\.?\s*VAT\)", text, re.IGNORECASE):
            values.append(float(m.group(1)))
        if not values:
            return None
        return pence_to_pounds(min(values))

    @staticmethod
    def parse_quote_row(text: str) -> dict[str, float] | None:
        """The site's own quote line: the quantity, rate and totals it states.

        Read so the figure reported is what Scottish Fuels actually quoted —
        including its own inclusive total for the quantity it quoted — rather
        than our multiplication of a per-litre rate by the quantity we asked
        for. ``None`` when no full row is present, and the per-litre parse is
        used instead.
        """
        match = QUOTE_ROW_RE.search(text)
        if not match:
            return None
        quantity, ppl, ex_vat, vat, inc_vat = match.groups()
        return {
            "quantity": int(quantity),
            "price_ex_vat": pence_to_pounds(float(ppl)),
            "ex_vat_total": float(ex_vat.replace(",", "")),
            "vat": float(vat.replace(",", "")),
            "inc_vat_total": float(inc_vat.replace(",", "")),
        }

    @staticmethod
    def set_quantity(driver, quantity_liters: int) -> int | None:
        """Set the order size without emptying the field, and say what stuck.

        The control arrives pre-filled, and this page rewrites an emptied one to
        its own 500 L minimum, so clearing it changes what is being quoted. It
        is therefore only touched when it differs from what was asked, and then
        through the DOM with the events the page listens for. Returns the value
        the control holds afterwards — the page clamps to its own min/max/step —
        or ``None`` when there is no quantity control at all.
        """
        from selenium.webdriver.common.by import By

        control = driver.find_element(By.CSS_SELECTOR, "input[name='quantity']")
        wanted = str(quantity_liters)
        if (control.get_attribute("value") or "") != wanted:
            driver.execute_script(
                "arguments[0].value = arguments[1];"
                "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
                control,
                wanted,
            )
        current = control.get_attribute("value") or ""
        return int(current) if current.isdigit() else None

    def _wait_for_quote_form(self, driver) -> None:
        """Wait for the quote form (or a bounced sign-in page) to render.

        Replaces a flat 6s sleep: the fuel-type radio and quantity control are
        the readiness signal, and a redirect to the account page counts too, so
        an expired session is noticed at once rather than after the full wait.
        """
        from selenium.webdriver.common.by import By

        wait_until(
            lambda: self.is_login_page(driver.current_url)
            or bool(
                driver.find_elements(
                    By.CSS_SELECTOR,
                    "input[name='productSelection'], input[name='quantity']",
                )
            ),
            what="the Scottish Fuels quote form",
        )

    def _wait_for_quote_result(self, driver) -> None:
        """Wait until the fresh quote (or a mid-quote redirect) is on the page.

        Replaces a flat 15s sleep. The result is a server-rendered price, so the
        page text is the readiness signal; a redirect back to sign-in is the
        other way this ends, and is reported by the caller.
        """
        wait_until(
            lambda: self.is_login_page(driver.current_url)
            or self.parse_ppl(self._body_text(driver)) is not None,
            what="the Scottish Fuels quote result",
        )

    @staticmethod
    def _body_text(driver) -> str:
        """The rendered body text, read through the driver's own finder."""
        from selenium.webdriver.common.by import By

        return driver.find_element(By.TAG_NAME, "body").text

    def _manual(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        notes: str,
        reason: str | None = None,
        *,
        status: str = "manual_action_required",
    ) -> QuoteResult:
        """A non-quote result, optionally classified.

        ``reason`` stays optional because the callers here do not all know one:
        an unrecognised case reports ``None`` — "unclassified" — rather than a
        guess that a consumer would then branch on.

        ``status`` is ``manual_action_required`` except for the run that raised,
        where ``reason="site_error"`` says the attempt itself fell over and the
        row has to read as one: the consumers split a supplier that gave no price
        from one whose retrieval failed, and only the second is a fault to look
        at.
        """
        # The phone on the record is contact data, not a route this app offers:
        # it never rings a supplier, so the note names an address or a page.
        contact = ", ".join(p for p in [supplier.get("email"), supplier.get("website")] if p)
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status=status,
            reason=reason,
            source="scottish_fuels_browser",
            notes=f"{notes} Contact: {contact}" if contact else notes,
        )

