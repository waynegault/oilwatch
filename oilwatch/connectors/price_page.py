from __future__ import annotations

import re
from typing import Any

import httpx

from oilwatch.connectors.base import BaseConnector
from oilwatch.models import OrderResult, QuoteResult
from oilwatch.pricing import apply_vat, inclusive_total, normalise_price_per_litre


class PricePageConnector(BaseConnector):
    def __init__(self) -> None:
        self.client = httpx.Client(
            follow_redirects=True,
            headers={"User-Agent": "OilWatch/0.1 (+https://github.com/)"},
            timeout=20.0,
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
            raise ValueError(f"Supplier {supplier['name']} is missing price_regex configuration.")
        response = self.client.get(quote_url)
        response.raise_for_status()
        match = re.search(pattern, response.text, re.IGNORECASE)
        if not match:
            raise ValueError(f"No price matched on {quote_url}")
        price = self._normalise_price(match.group(1), config)
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
            notes="This supplier exposes pricing on a page but does not have automated ordering configured.",
        )

    @staticmethod
    def _normalise_price(text: str, config: dict[str, Any]) -> float:
        price = normalise_price_per_litre(text)
        if price is None:
            raise ValueError(f"Could not parse price from {text!r}")
        # The scraped value is assumed to already include VAT unless the
        # supplier config declares an ex-VAT rate to apply.
        vat_rate = config.get("vat_rate")
        if vat_rate:
            price = apply_vat(price, float(vat_rate))
        return price

