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
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from oilwatch.connectors.base import BaseConnector
from oilwatch.models import QuoteResult
from oilwatch.pricing import inclusive_price_and_total, inclusive_total


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

    Subclasses set ``source``, ``price_description`` and ``no_price_note``, and
    implement :meth:`collect_price`, which drives the page and returns
    ``(ex_vat_price, raw_payload)`` — with ``None`` for the price when the page
    yielded nothing parseable, so the caller quotes manually rather than
    fabricating one.
    """

    source = ""
    price_description = ""
    no_price_note = "Could not extract a price from the page."
    #: Whether :meth:`collect_price` returns an already-inclusive per-litre price
    #: (the supplier's "total you pay") or an ex-VAT one the base lifts by the
    #: domestic rate. Default is the ex-VAT basis.
    price_is_inclusive = False

    @abstractmethod
    def collect_price(
        self,
        page: Any,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> tuple[float | None, dict[str, Any]]:
        """Drive ``page`` and return the per-litre price and raw payload.

        The price is ex-VAT unless ``price_is_inclusive`` is set, in which case
        it is already the standard-delivery total per litre.
        """
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
            return self._manual(
                supplier,
                quantity_liters,
                f"Browser automation error: {exc}",
                "site_error",
                status="error",
            )

        if ex_vat_price is None:
            return self._manual(supplier, quantity_liters, self.no_price_note, "no_price_found")

        if self.price_is_inclusive:
            price_per_liter = round(ex_vat_price, 4)
            total_price = inclusive_total(price_per_liter, quantity_liters)
            basis_note = f"standard-delivery total £{price_per_liter:.4f}/L inc VAT"
        else:
            price_per_liter, total_price = inclusive_price_and_total(ex_vat_price, quantity_liters)
            basis_note = f"ex-VAT £{ex_vat_price:.4f}/L, inc-VAT £{price_per_liter:.4f}/L"

        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="ok",
            price_per_liter=price_per_liter,
            total_price=total_price,
            source=self.source,
            notes=(
                f"Price from {self.price_description} for {quantity_liters}L "
                f"({basis_note}). Postcode: {postcode or 'not provided'}"
            ),
            raw_payload=raw_payload,
        )

    def _manual(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        notes: str,
        reason: str,
        *,
        status: str = "manual_action_required",
    ) -> QuoteResult:
        """A quote this connector cannot give, with the reason it cannot.

        ``reason`` is required: both callers know which case they are in, and a
        default here would be guessing where the row must not be a guess.

        ``status`` is ``manual_action_required`` for everything the supplier
        itself decided — no price on the page, a portal that wants a sign-in —
        and ``error`` for the one case where the attempt fell over, which is what
        ``reason="site_error"`` means. The two are kept apart because a consumer
        branches on them: ``service`` splits the last ask per supplier into the
        ones with no price to give ("often doing exactly what it does") and the
        ones worth looking at, so a browser that would not launch reported as the
        first reads as a supplier with nothing to say.
        """
        # The phone on the record is contact data, not a route this app offers:
        # it never rings a supplier, so the note names an address or a page and
        # leaves the number off.
        contact = ", ".join(
            p for p in [supplier.get("email"), supplier.get("website")] if p
        )
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status=status,
            reason=reason,
            source=self.source,
            notes=f"{notes} Contact: {contact}" if contact else notes,
        )

