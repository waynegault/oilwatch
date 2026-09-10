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

from oilwatch.connectors.base import BaseConnector
from oilwatch.identity import load_contact
from oilwatch.models import OrderResult, QuoteResult
from oilwatch.pricing import DOMESTIC_VAT_RATE, apply_vat, inclusive_total, pence_to_pounds


class RixBrowserConnector(BaseConnector):
    quote_url = "https://fuelquote.rix.co.uk/"

    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        postcode = context.get("postcode", "") or ""
        email = context.get("email", "") or load_contact().email
        # Ofcom drama number, not a real one: Rix requires a phone before it
        # will quote, and it is never used for anything but filling the form.
        phone = context.get("phone", "") or "07700 900123"

        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page()
                try:
                    page.goto(self.quote_url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(4000)
                    self._fill_form(page, quantity_liters, postcode, email, phone)
                    page.click("button[type='submit']", timeout=5000)
                    page.wait_for_timeout(15000)
                    results_text = page.eval_on_selector("body", "el => el.innerText")
                    results_url = page.url
                finally:
                    browser.close()
        except Exception as exc:  # noqa: BLE001
            return self._manual(supplier, quantity_liters, f"Browser automation error: {exc}")

        ex_vat_price = self.parse_ppl(results_text)
        if ex_vat_price is None:
            return self._manual(supplier, quantity_liters, "Could not extract a price from the Rix results page.")

        price_per_liter = apply_vat(ex_vat_price, DOMESTIC_VAT_RATE)
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="ok",
            price_per_liter=price_per_liter,
            total_price=inclusive_total(price_per_liter, quantity_liters),
            source="rix_browser",
            notes=f"Price from Rix quote tool for {quantity_liters}L (ex-VAT £{ex_vat_price:.4f}/L, inc-VAT £{price_per_liter:.4f}/L). Postcode: {postcode or 'not provided'}",
            raw_payload={"quote_url": self.quote_url, "results_url": results_url, "postcode": postcode, "price_ex_vat": ex_vat_price},
        )

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

    def _manual(self, supplier: dict[str, Any], quantity_liters: int, notes: str) -> QuoteResult:
        contact = ", ".join(p for p in [supplier.get("phone"), supplier.get("email"), supplier.get("website")] if p)
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="manual_action_required",
            source="rix_browser",
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
            notes="Order via the Rix quote tool or by phone (0800 542 4207).",
        )
