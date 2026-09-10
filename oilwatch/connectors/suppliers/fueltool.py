"""Fueltool connector - UK-average heating oil price tracker.

Fueltool is a comparison site rather than a supplier, but its homepage renders a
server-side "Fueltool average today" figure in pence per litre (ex-VAT, for a
1,000L order). It is useful as a daily market benchmark for the trend signal.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from oilwatch.connectors.base import BaseConnector
from oilwatch.http import build_client
from oilwatch.models import OrderResult, QuoteResult
from oilwatch.pricing import DOMESTIC_VAT_RATE, apply_vat, inclusive_total, pence_to_pounds


class FueltoolConnector(BaseConnector):
    def __init__(self) -> None:
        self.client = build_client()
        self.quote_url = "https://www.fueltool.co.uk/"

    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        try:
            response = self.client.get(self.quote_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="error",
                source="fueltool",
                notes=f"HTTP error fetching quote: {exc}",
            )

        ex_vat_price = self._extract_average(response.text)
        if ex_vat_price is None:
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="manual_action_required",
                source="fueltool",
                notes=f"Could not extract the Fueltool average price from {self.quote_url}",
            )

        price_per_liter = apply_vat(ex_vat_price, DOMESTIC_VAT_RATE)
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="ok",
            price_per_liter=price_per_liter,
            total_price=inclusive_total(price_per_liter, quantity_liters),
            source="fueltool",
            notes=f"Fueltool UK average (ex-VAT £{ex_vat_price:.4f}/L, inc-VAT £{price_per_liter:.4f}/L). Benchmark, not a local supplier quote.",
            raw_payload={"url": self.quote_url, "price_ex_vat": ex_vat_price, "kind": "uk_average"},
        )

    @staticmethod
    def _extract_average(html: str) -> float | None:
        """Extract the 'Fueltool average' pence figure from the homepage."""
        # e.g. "Fueltool average today: 98.73p per litre (ex VAT, 1,000L)"
        patterns = [
            r"[Aa]verage\s*today[^<]*?(\d{2,3}(?:\.\d{1,2})?)\s*p\s*(?:per\s*/?\s*litre)",
            r"Fueltool\s*[Aa]verage[^<]*?(\d{2,3}(?:\.\d{1,2})?)\s*p",
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if match:
                return pence_to_pounds(match.group(1))
        return None

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
            notes="Fueltool is a comparison site; order directly with the supplier.",
        )
