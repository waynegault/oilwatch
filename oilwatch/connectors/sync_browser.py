"""Shared plumbing for connectors that drive a supplier site with Playwright's
synchronous API (Fuelsoft, Rix).

These cannot reuse :class:`~oilwatch.connectors.browser_base.BrowserConnector`,
which is asynchronous, but they share its concerns: launching a headless
browser, shaping an ``ok`` quote, and falling back to ``manual_action_required``.
Keeping that here means one place sets the launch arguments and one place builds
the fallback. Playwright is imported inside :func:`sync_page`, so importing a
connector module stays cheap and does not require a browser.
"""

from __future__ import annotations

from abc import abstractmethod
from contextlib import contextmanager
from typing import Any, Iterator

from oilwatch.connectors.base import BaseConnector
from oilwatch.models import QuoteResult
from oilwatch.pricing import DOMESTIC_VAT_RATE, apply_vat, inclusive_total


@contextmanager
def sync_page(headless: bool = True) -> Iterator[Any]:
    """Yield a Playwright sync page, closing the browser on exit."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        try:
            yield browser.new_page()
        finally:
            browser.close()


class SyncBrowserConnector(BaseConnector):
    """Base for the synchronous Playwright connectors.

    Subclasses set ``source``, ``price_description``, ``no_price_note`` and
    ``order_notes``, and implement :meth:`collect_price`, which drives the page
    and returns ``(ex_vat_price, raw_payload)`` — with ``None`` for the price
    when the page yielded nothing parseable, so the caller quotes manually
    rather than fabricating one.
    """

    source = ""
    price_description = ""
    no_price_note = "Could not extract a price from the page."
    order_notes = "Order via the supplier's site or by phone."

    @abstractmethod
    def collect_price(
        self,
        page: Any,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> tuple[float | None, dict[str, Any]]:
        """Drive ``page`` and return the ex-VAT price-per-litre and raw payload."""
        raise NotImplementedError

    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        postcode = context.get("postcode", "") or ""
        try:
            with sync_page() as page:
                ex_vat_price, raw_payload = self.collect_price(
                    page, supplier, quantity_liters, context
                )
        except Exception as exc:  # noqa: BLE001
            return self._manual(supplier, quantity_liters, f"Browser automation error: {exc}")

        if ex_vat_price is None:
            return self._manual(supplier, quantity_liters, self.no_price_note)

        price_per_liter = apply_vat(ex_vat_price, DOMESTIC_VAT_RATE)
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="ok",
            price_per_liter=price_per_liter,
            total_price=inclusive_total(price_per_liter, quantity_liters),
            source=self.source,
            notes=(
                f"Price from {self.price_description} for {quantity_liters}L "
                f"(ex-VAT £{ex_vat_price:.4f}/L, inc-VAT £{price_per_liter:.4f}/L). "
                f"Postcode: {postcode or 'not provided'}"
            ),
            raw_payload=raw_payload,
        )

    def _manual(self, supplier: dict[str, Any], quantity_liters: int, notes: str) -> QuoteResult:
        contact = ", ".join(
            p for p in [supplier.get("phone"), supplier.get("email"), supplier.get("website")] if p
        )
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="manual_action_required",
            source=self.source,
            notes=f"{notes} Contact: {contact or 'supplier website'}",
        )

