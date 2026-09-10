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
from oilwatch.models import OrderResult, QuoteResult
from oilwatch.pricing import DOMESTIC_VAT_RATE, apply_vat, inclusive_total, pence_to_pounds

log = get_logger("connectors.scottish_fuels")

# Substrings that mean "we were bounced to the sign-in screen". Deliberately
# narrow: the signed-in account dashboard also lives under /customer/account/,
# so a bare "/customer/account" test would call a working session a failure.
LOGIN_PAGE_MARKERS = ("/customer/account/login", "/login")

# Labels that identify a kerosene-type product when the configured SKU is gone.
KEROSENE_LABELS = ("kerosene", "heating oil", "premium")


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
            driver = auth.launch(headless=True)
            try:
                driver.get(self.quote_url)
                time.sleep(6)

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
                        )
                    if not signed_in:
                        return self._manual(
                            supplier,
                            quantity_liters,
                            "Session expired and the automatic sign-in did not take "
                            "(likely a CAPTCHA challenge). Run `oilwatch login "
                            "scottish_fuels` by hand.",
                        )
                    driver.get(self.quote_url)
                    time.sleep(6)
                    if self.is_login_page(driver.current_url):
                        return self._manual(
                            supplier,
                            quantity_liters,
                            "Signed in but /quote/ still redirects to the account page.",
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
                    )
                driver.execute_script(
                    "arguments[0].click();",
                    driver.find_element(
                        By.CSS_SELECTOR, f"input[name='productSelection'][value='{chosen_sku}']"
                    ),
                )
                time.sleep(0.5)

                # quantity
                qty = driver.find_element(By.CSS_SELECTOR, "input[name='quantity']")
                qty.clear()
                qty.send_keys(str(quantity_liters))
                time.sleep(0.5)

                # Get Quote
                button = driver.find_element(By.XPATH, "//button[contains(., 'Get Quote')]")
                driver.execute_script("arguments[0].click();", button)
                time.sleep(15)

                body_text = driver.find_element(By.TAG_NAME, "body").text
                final_url = driver.current_url
            finally:
                auth.close()
        except Exception as exc:  # noqa: BLE001
            return self._manual(supplier, quantity_liters, f"Browser automation error: {exc}")

        if final_url and self.is_login_page(final_url):
            return self._manual(
                supplier,
                quantity_liters,
                "Session expired mid-quote (redirected back to "
                "/customer/account/). Run `oilwatch login scottish_fuels` and retry.",
            )

        if not self.is_logged_in(body_text):
            return self._manual(
                supplier,
                quantity_liters,
                "Not signed in. Run `oilwatch login scottish_fuels` once to establish a session.",
            )

        ex_vat_price = self.parse_ppl(body_text)
        if ex_vat_price is None:
            return self._manual(supplier, quantity_liters, "Could not find a price on the quote result page.")

        price_per_liter = apply_vat(ex_vat_price, DOMESTIC_VAT_RATE)
        sku_note = "" if chosen_sku == configured_sku else f" (configured {configured_sku}, used {chosen_sku})"
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="ok",
            price_per_liter=price_per_liter,
            total_price=inclusive_total(price_per_liter, quantity_liters),
            source="scottish_fuels_browser",
            notes=(
                f"Fresh quote from Scottish Fuels for {quantity_liters}L "
                f"(ex-VAT £{ex_vat_price:.4f}/L, inc-VAT £{price_per_liter:.4f}/L). "
                f"Postcode: {postcode}. Product SKU: {chosen_sku}{sku_note}."
            ),
            raw_payload={
                "quote_url": self.quote_url,
                "postcode": postcode,
                "price_ex_vat": ex_vat_price,
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
            try:
                values.append(float(m.group(1)))
            except ValueError:
                continue
        if not values:
            return None
        return pence_to_pounds(min(values))

    def _manual(self, supplier: dict[str, Any], quantity_liters: int, notes: str) -> QuoteResult:
        contact = ", ".join(p for p in [supplier.get("phone"), supplier.get("email"), supplier.get("website")] if p)
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="manual_action_required",
            source="scottish_fuels_browser",
            notes=f"{notes} Contact: {contact or 'supplier website'}",
        )

    def place_order(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        agreed_price_per_liter: float,
        context: dict[str, Any],
    ) -> OrderResult:
        return OrderResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            created_at=self.now(),
            quantity_liters=quantity_liters,
            agreed_price_per_liter=agreed_price_per_liter,
            status="manual_action_required",
            notes="Order via the Scottish Fuels quote portal or by phone (0345 300 8844).",
        )
