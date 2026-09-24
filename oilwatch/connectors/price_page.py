from __future__ import annotations

import re
from typing import Any

import httpx

from oilwatch.connectors.base import BaseConnector
from oilwatch.http import build_client
from oilwatch.models import QuoteResult
from oilwatch.pricing import apply_vat, inclusive_total, normalise_price_per_litre


class PricePageConnector(BaseConnector):
    def __init__(self) -> None:
        # Shared client (see oilwatch/http.py): timeouts plus transport-level
        # retries. Keeps the honest OilWatch user agent rather than a browser's.
        self.client = build_client(
            timeout=20.0,
            headers={"User-Agent": "OilWatch/0.1 (+https://github.com/)"},
        )

    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        config = supplier.get("connector_config", {})
        quote_url = config.get("quote_url") or supplier["website"]
        pattern = config.get("price_regex")
        if not pattern:
            # The one failure that still raises, because there is no honest row
            # for it: every reason the contract defines describes what a *site*
            # did, and this is a register entry that names no price at all. A row
            # reading `no_price_found` would send the owner to inspect a page that
            # is doing nothing wrong. `quote-all` turns the raise into an error
            # row regardless; `quote <id>` names the missing setting outright.
            raise ValueError(f"Supplier {supplier['name']} is missing price_regex configuration.")

        try:
            response = self.http_client().get(quote_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return self._manual(
                supplier,
                quantity_liters,
                f"HTTP error fetching {quote_url}: {exc}",
                "site_error",
                status="error",
            )

        match = re.search(pattern, response.text, re.IGNORECASE)
        if not match:
            # The page answered and carried nothing this pattern could read, which
            # is what no_price_found means — not a fault in the retrieval.
            return self._manual(
                supplier,
                quantity_liters,
                f"No price matched on {quote_url}.",
                "no_price_found",
            )

        price = self._normalise_price(match.group(1), config)
        if price is None:
            # It matched and could not be read as a number: a parse failure, and
            # so `site_error` rather than "no price on the page".
            return self._manual(
                supplier,
                quantity_liters,
                f"Could not read {match.group(1)!r} on {quote_url} as a price.",
                "site_error",
                status="error",
            )
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="ok",
            price_per_liter=price,
            total_price=inclusive_total(price, quantity_liters),
            source="price_page",
            raw_payload={"quote_url": quote_url},
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

        ``status`` is ``manual_action_required`` when the page itself decided —
        it answered and had no price — and ``error`` when the attempt fell over,
        which is what ``reason="site_error"`` says. A consumer branches on the
        difference, so a dead site reported as "nothing to give" sends the reader
        to the wrong next step.
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
            source="price_page",
            notes=f"{notes} Contact: {contact}" if contact else notes,
        )

    @staticmethod
    def _normalise_price(text: str, config: dict[str, Any]) -> float | None:
        """The captured text as GBP per litre, or ``None`` if it is not a number."""
        price = normalise_price_per_litre(text)
        if price is None:
            return None
        # The scraped value is assumed to already include VAT unless the
        # supplier config declares an ex-VAT rate to apply.
        vat_rate = config.get("vat_rate")
        if vat_rate:
            price = apply_vat(price, float(vat_rate))
        return price

