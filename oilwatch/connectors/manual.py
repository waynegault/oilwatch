from __future__ import annotations

from typing import Any

from oilwatch.connectors.base import BaseConnector
from oilwatch.models import OrderResult, QuoteResult


class ManualConnector(BaseConnector):
    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        contact_parts = [supplier.get("phone"), supplier.get("email"), supplier.get("website")]
        contact = ", ".join(part for part in contact_parts if part)
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="manual_action_required",
            source="manual",
            notes=f"Manual quote required for {quantity_liters}L. Contact via: {contact or 'supplier website'}",
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
            notes="Manual order placement required. Use stored supplier contact details.",
        )

