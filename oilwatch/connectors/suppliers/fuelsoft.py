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

from oilwatch.connectors.sync_browser import SyncBrowserConnector
from oilwatch.identity import load_contact


class FuelsoftConnector(SyncBrowserConnector):
    product_value = "003"  # KERO

    source = "fuelsoft"
    price_description = "Fuelsoft quote form"
    no_price_note = "Could not extract a price from the quote response."
    order_notes = "Order via the supplier's Fuelsoft portal or by phone."

    def collect_price(
        self,
        page: Any,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> tuple[float | None, dict[str, Any]]:
        postcode = context.get("postcode", "") or ""
        email = context.get("email", "") or load_contact().email
        address_line1 = context.get("home_label", "") or "Hatton of Fintray"
        config = supplier.get("connector_config") or {}
        quote_url = config.get("quote_url") or supplier.get("website", "")
        product_value = config.get("product_value", self.product_value)

        captured: dict[str, Any] = {}

        def on_response(resp) -> None:
            # Johnston Oils uses .../quote/..., Regency uses .../quoteAll/...
            if "Quotes/deliveryschedules/quote" in resp.url:
                try:
                    captured["body"] = resp.json()
                except Exception:  # noqa: BLE001
                    captured["body"] = resp.text()

        page.on("response", on_response)
        page.goto(quote_url, wait_until="domcontentloaded", timeout=60000)
        self._dismiss_cookie_dialog(page)
        self._fill_form(page, postcode, address_line1, email, quantity_liters, product_value)
        self._get_quote(page)
        page.wait_for_timeout(8000)  # wait for the quote API response

        ex_vat_price = self.parse_quote_response(captured.get("body"))
        raw_payload = {"quote_url": quote_url, "postcode": postcode, "price_ex_vat": ex_vat_price}
        return ex_vat_price, raw_payload

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
