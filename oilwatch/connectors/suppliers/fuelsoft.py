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
price. Each best-effort step logs why it was skipped, so a failed run can be
diagnosed instead of only showing the final "manual" fallback.
"""

from __future__ import annotations

from typing import Any

from oilwatch.connectors.sync_browser import SyncBrowserConnector
from oilwatch.identity import load_contact
from oilwatch.logging_setup import get_logger

log = get_logger("connectors.fuelsoft")

COOKIE_SELECTORS = [
    ".dialogWindow button",
    "button:has-text('Confirm')",
    "button:has-text('Accept')",
    "button:has-text('Accept all')",
    "button:has-text('OK')",
    "button:has-text('Continue')",
]

# Wizard sections that start hidden and must be revealed before their controls
# can be driven.
HIDDEN_SECTIONS = (
    "mainContent_buttonGetProducts",
    "mainContent_productSection",
    "mainContent_deliveryOptionSection",
)


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
        # The form's first address line is the home label up to its first comma.
        # A literal here would both publish the address in source and drift from
        # config/settings.json; an empty label simply leaves the field blank.
        address_line1 = (context.get("home_label", "") or "").split(",")[0].strip()
        config = supplier.get("connector_config") or {}
        quote_url = config.get("quote_url") or supplier.get("website", "")
        product_value = config.get("product_value", self.product_value)

        captured: dict[str, Any] = {}

        def on_response(resp) -> None:
            # Johnston Oils uses .../quote/..., Regency uses .../quoteAll/...
            if "Quotes/deliveryschedules/quote" in resp.url:
                try:
                    captured["body"] = resp.json()
                except Exception as exc:  # noqa: BLE001
                    log.debug("quote response was not JSON, using text: %s", exc)
                    captured["body"] = resp.text()

        page.on("response", on_response)
        page.goto(quote_url, wait_until="domcontentloaded", timeout=60000)
        self._dismiss_cookie_dialog(page)
        self._fill_form(page, postcode, address_line1, email, quantity_liters, product_value)
        self._get_quote(page)
        self._wait_for_quote_body(page, captured)

        ex_vat_price = self.parse_quote_response(captured.get("body"))
        if ex_vat_price is None:
            # Say what came back: "no price in the response" and "no response at
            # all" produced the same manual note, and the body's shape is what a
            # realignment has to be written against.
            body = captured.get("body")
            log.warning(
                "no PPL in the Fuelsoft quote response from %s; body (%s):\n%.1500s",
                quote_url,
                "captured" if body is not None else "never captured",
                body if body is not None else "",
            )
        raw_payload = {"quote_url": quote_url, "postcode": postcode, "price_ex_vat": ex_vat_price}
        return ex_vat_price, raw_payload

    @staticmethod
    def _dismiss_cookie_dialog(page) -> None:
        page.wait_for_timeout(2500)
        for selector in COOKIE_SELECTORS:
            try:
                if page.query_selector(selector):
                    page.click(selector, timeout=3000)
                    page.wait_for_timeout(1000)
                    return
            except Exception as exc:  # noqa: BLE001
                log.debug("cookie selector %r not clickable: %s", selector, exc)
                continue
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        except Exception as exc:  # noqa: BLE001
            log.debug("cookie dialog escape failed: %s", exc)

    def _fill_form(self, page, postcode: str, address_line1: str, email: str, quantity_liters: int, product_value: str) -> None:
        # Reveal the manual address fields.
        try:
            if page.query_selector("#btnEnterAddressManually"):
                page.click("#btnEnterAddressManually", timeout=5000)
                page.wait_for_timeout(1000)
        except Exception as exc:  # noqa: BLE001
            log.debug("could not reveal manual address fields: %s", exc)

        for selector, value in (("#txtPostcode", postcode), ("#txtDelAdd1", address_line1), ("#txtEmail", email)):
            if not value:
                continue
            try:
                field = page.query_selector(selector)
                if field:
                    field.fill(value, timeout=5000)
                    page.wait_for_timeout(500)
            except Exception as exc:  # noqa: BLE001
                log.debug("could not fill %s: %s", selector, exc)
                continue

        # The product/quote/delivery sections are hidden in a WebForms wizard;
        # reveal them so the remaining controls can be driven.
        for section_id in HIDDEN_SECTIONS:
            try:
                page.evaluate(f"document.getElementById('{section_id}').style.display='block'")
            except Exception as exc:  # noqa: BLE001
                log.debug("could not reveal section %s: %s", section_id, exc)
        page.wait_for_timeout(300)

        try:
            if page.query_selector("#btnGetProducts"):
                page.click("#btnGetProducts", timeout=5000)
                page.wait_for_timeout(5000)
        except Exception as exc:  # noqa: BLE001
            log.debug("get-products step failed: %s", exc)

        try:
            page.select_option("#mainContent_lstProduct", product_value, timeout=5000)
            page.wait_for_timeout(1200)
        except Exception as exc:  # noqa: BLE001
            log.debug("could not select product %s: %s", product_value, exc)

        try:
            page.fill("#txtQty", str(quantity_liters), timeout=5000)
            page.wait_for_timeout(500)
        except Exception as exc:  # noqa: BLE001
            log.debug("could not fill quantity: %s", exc)

        try:
            page.select_option("#mainContent_lstDeliveryOption", index=1, timeout=5000)
            page.wait_for_timeout(1000)
        except Exception as exc:  # noqa: BLE001
            log.debug("could not select delivery option: %s", exc)

    @staticmethod
    def _get_quote(page) -> None:
        try:
            page.click("#btnGetQuote", timeout=5000)
        except Exception as exc:  # noqa: BLE001
            log.debug("could not click Get Quote: %s", exc)

    @staticmethod
    def _wait_for_quote_body(page, captured: dict[str, Any], timeout_ms: int = 20000) -> None:
        """Wait until the quote API response has been captured, bounded.

        The response is captured by the ``on_response`` handler registered before
        navigation (so it cannot be missed), and this replaces a flat 8s sleep
        with a poll that returns as soon as the body arrives. Absence is not
        fatal — the caller falls back to a manual quote — so the wait is simply
        given up on when it elapses.
        """
        waited = 0
        while "body" not in captured and waited < timeout_ms:
            page.wait_for_timeout(250)
            waited += 250
        if "body" not in captured:
            log.debug("no Fuelsoft quote response after %d ms", timeout_ms)

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
