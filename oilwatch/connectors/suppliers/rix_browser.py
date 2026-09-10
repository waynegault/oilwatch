"""Rix browser connector.

Rix's live quote tool is a Remix app embedded at ``fuelquote.rix.co.uk`` (shown
in an iframe on rix.co.uk/homes/fuel-quote). The price is computed server-side
and rendered on a results page (``/your-quote/{id}``) after submitting the
quote form. This connector drives that form with Playwright and reads the
``PPL (ex. VAT)`` figure from the results page.

Note: the price API itself is called server-side by the Remix app, so there is
no client-side endpoint to hit directly; browser automation is required.
"""

from __future__ import annotations

import re
from typing import Any

from oilwatch.connectors.sync_browser import SyncBrowserConnector
from oilwatch.identity import load_contact
from oilwatch.logging_setup import get_logger
from oilwatch.pricing import pence_to_pounds

log = get_logger("connectors.rix")


class RixBrowserConnector(SyncBrowserConnector):
    quote_url = "https://fuelquote.rix.co.uk/"

    source = "rix_browser"
    price_description = "Rix quote tool"
    no_price_note = "Could not extract a price from the Rix results page."
    order_notes = "Order via the Rix quote tool or by phone (0800 542 4207)."

    def collect_price(
        self,
        page: Any,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> tuple[float | None, dict[str, Any]]:
        postcode = context.get("postcode", "") or ""
        email = context.get("email", "") or load_contact().email
        # Ofcom drama number, not a real one: Rix requires a phone before it
        # will quote, and it is never used for anything but filling the form.
        phone = context.get("phone", "") or "07700 900123"

        page.goto(self.quote_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        self._fill_form(page, quantity_liters, postcode, email, phone)
        page.click("button[type='submit']", timeout=5000)
        self._wait_for_results(page)
        results_text = page.eval_on_selector("body", "el => el.innerText")
        results_url = page.url

        ex_vat_price = self.parse_ppl(results_text)
        raw_payload = {
            "quote_url": self.quote_url,
            "results_url": results_url,
            "postcode": postcode,
            "price_ex_vat": ex_vat_price,
        }
        return ex_vat_price, raw_payload

    @staticmethod
    def _wait_for_results(page, timeout_ms: int = 20000) -> None:
        """Wait for the results page to show a PPL figure, bounded.

        Replaces a flat 15s sleep: this returns as soon as the price text appears.
        A page that never shows one is not fatal — the caller reports a manual
        quote — so a timeout is logged rather than raised.
        """
        try:
            page.wait_for_function(
                r"() => /PPL\s*\(ex\.?\s*VAT\)/i.test(document.body.innerText)",
                timeout=timeout_ms,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("Rix results did not show a PPL within %d ms: %s", timeout_ms, exc)

    @staticmethod
    def _fill_form(page, quantity_liters: int, postcode: str, email: str, phone: str) -> None:
        # fuel type is a native <select> behind a custom radix dropdown; set the
        # native value directly so the remix-validated-form state picks it up.
        page.select_option("select[name='fuelType']", value="kero", timeout=5000)
        page.wait_for_timeout(800)

        page.fill("input[name='litres']", str(quantity_liters))
        page.wait_for_timeout(400)
        page.fill("input[name='postcode']", postcode)
        page.wait_for_timeout(1500)  # allow async postcode validation
        page.fill("input[name='email']", email)
        page.wait_for_timeout(400)
        page.fill("input[name='phoneNumber']", phone)
        page.wait_for_timeout(400)
        page.click("#privacy", timeout=5000)
        page.wait_for_timeout(800)

    @staticmethod
    def parse_ppl(text: str) -> float | None:
        """Extract the cheapest ex-VAT price-per-litre (pence) from results.

        The results page lists one block per delivery option, each with
        ``PPL (ex. VAT)`` followed by a pence figure (e.g. ``110.35p``).
        Return the cheapest.
        """
        pence_values = []
        for m in re.finditer(r"PPL\s*\(ex\.?\s*VAT\)\s*([0-9]+(?:\.[0-9]+)?)\s*p", text, re.IGNORECASE):
            try:
                pence_values.append(float(m.group(1)))
            except ValueError:
                continue
        if not pence_values:
            return None
        return pence_to_pounds(min(pence_values))
