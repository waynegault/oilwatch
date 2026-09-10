"""Fuelsoft platform connector (Connon Bros, Johnson Oils).

Several local suppliers use the Fuelsoft "WEBPLUS / WebOrdering" ASP.NET quote
form. It has no server-rendered price - the quote is computed after entering a
delivery postcode and product, via a JSON API behind the page
(``Quotes/deliveryschedules/quote/...``). This connector drives the form with
Playwright and reads the ``PPL`` (price-per-litre, ex-VAT) from that API's JSON
response.

The form is a fragile, stateful WebForms page (cookie dialog, hidden fields),
so every step is best-effort: on any failure the connector returns
``manual_action_required`` with the supplier contact details rather than a bogus
price.
"""

from __future__ import annotations

from typing import Any

from oilwatch.connectors.base import BaseConnector
from oilwatch.identity import load_contact
from oilwatch.models import OrderResult, QuoteResult
from oilwatch.pricing import DOMESTIC_VAT_RATE, apply_vat, inclusive_total


class FuelsoftConnector(BaseConnector):
    product_value = "003"  # KERO

    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        postcode = context.get("postcode", "") or ""
        email = context.get("email", "") or load_contact().email
        address_line1 = context.get("home_label", "") or "Hatton of Fintray"
        config = supplier.get("connector_config") or {}
        quote_url = config.get("quote_url") or supplier.get("website", "")
        product_value = config.get("product_value", self.product_value)

        captured: dict[str, Any] = {}

        try:
            from playwright.sync_api import sync_playwright

            def on_response(resp) -> None:
                # Johnston Oils uses .../quote/..., Regency uses .../quoteAll/...
                if "Quotes/deliveryschedules/quote" in resp.url:
                    try:
                        captured["body"] = resp.json()
                    except Exception:  # noqa: BLE001
                        captured["body"] = resp.text()

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page()
                page.on("response", on_response)
                try:
                    page.goto(quote_url, wait_until="domcontentloaded", timeout=60000)
                    self._dismiss_cookie_dialog(page)
                    self._fill_form(page, postcode, address_line1, email, quantity_liters, product_value)
                    self._get_quote(page)
                    page.wait_for_timeout(8000)  # wait for the quote API response
                finally:
                    browser.close()
        except Exception as exc:  # noqa: BLE001
            return self._manual(supplier, quantity_liters, f"Browser automation error: {exc}")

        ex_vat_price = self.parse_quote_response(captured.get("body"))
        if ex_vat_price is None:
            return self._manual(supplier, quantity_liters, "Could not extract a price from the quote response.")

        price_per_liter = apply_vat(ex_vat_price, DOMESTIC_VAT_RATE)
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="ok",
            price_per_liter=price_per_liter,
            total_price=inclusive_total(price_per_liter, quantity_liters),
            source="fuelsoft",
            notes=f"Price from Fuelsoft quote form for {quantity_liters}L (ex-VAT £{ex_vat_price:.4f}/L, inc-VAT £{price_per_liter:.4f}/L). Postcode: {postcode or 'not provided'}",
            raw_payload={"quote_url": quote_url, "postcode": postcode, "price_ex_vat": ex_vat_price},
        )

    @staticmethod
    def _dismiss_cookie_dialog(page) -> None:
        page.wait_for_timeout(2500)
        for selector in [".dialogWindow button", "button:has-text('Confirm')", "button:has-text('Accept')", "button:has-text('Accept all')", "button:has-text('OK')", "button:has-text('Continue')"]:
            try:
                if page.query_selector(selector):
                    page.click(selector, timeout=3000)
                    page.wait_for_timeout(1000)
                    return
            except Exception:  # noqa: BLE001
                continue
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        except Exception:  # noqa: BLE001
            pass

    def _fill_form(self, page, postcode: str, address_line1: str, email: str, quantity_liters: int, product_value: str) -> None:
        # Reveal the manual address fields.
        try:
            if page.query_selector("#btnEnterAddressManually"):
                page.click("#btnEnterAddressManually", timeout=5000)
                page.wait_for_timeout(1000)
        except Exception:  # noqa: BLE001
            pass

        for selector, value in (("#txtPostcode", postcode), ("#txtDelAdd1", address_line1), ("#txtEmail", email)):
            if not value:
                continue
            try:
                field = page.query_selector(selector)
                if field:
                    field.fill(value, timeout=5000)
                    page.wait_for_timeout(500)
            except Exception:  # noqa: BLE001
                continue

        # The product/quote/delivery sections are hidden in a WebForms wizard;
        # reveal them so the remaining controls can be driven.
        for section_id in ("mainContent_buttonGetProducts", "mainContent_productSection", "mainContent_deliveryOptionSection"):
            try:
                page.evaluate(f"document.getElementById('{section_id}').style.display='block'")
            except Exception:  # noqa: BLE001
                pass
        page.wait_for_timeout(300)

        try:
            if page.query_selector("#btnGetProducts"):
                page.click("#btnGetProducts", timeout=5000)
                page.wait_for_timeout(5000)
        except Exception:  # noqa: BLE001
            pass

        try:
            page.select_option("#mainContent_lstProduct", product_value, timeout=5000)
            page.wait_for_timeout(1200)
        except Exception:  # noqa: BLE001
            pass

        try:
            page.fill("#txtQty", str(quantity_liters), timeout=5000)
            page.wait_for_timeout(500)
        except Exception:  # noqa: BLE001
            pass

        try:
            page.select_option("#mainContent_lstDeliveryOption", index=1, timeout=5000)
            page.wait_for_timeout(1000)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _get_quote(page) -> None:
        try:
            page.click("#btnGetQuote", timeout=5000)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def parse_quote_response(body: Any) -> float | None:
        """Extract the ex-VAT price-per-litre from the quote API JSON.

        The response is a list of delivery-option quote objects; each carries a
        ``PPL`` (price per litre, ex-VAT) field. Return the cheapest.
        """
        quotes = body if isinstance(body, list) else [body]
        ppls = []
        for q in quotes:
            if not isinstance(q, dict):
                continue
            ppl = q.get("PPL")
            if ppl is not None:
                try:
                    ppls.append(float(ppl))
                except (TypeError, ValueError):
                    continue
        if not ppls:
            return None
        return round(min(ppls), 4)

    def _manual(self, supplier: dict[str, Any], quantity_liters: int, notes: str) -> QuoteResult:
        contact = ", ".join(p for p in [supplier.get("phone"), supplier.get("email"), supplier.get("website")] if p)
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="manual_action_required",
            source="fuelsoft",
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
            notes="Order via the supplier's Fuelsoft portal or by phone.",
        )
