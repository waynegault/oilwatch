from __future__ import annotations

from typing import Any

from oilwatch.connectors import get_connector_for_supplier
from oilwatch.models import QuoteResult


class QuoteService:
    def __init__(self, currency: str, home_label: str) -> None:
        self.currency = currency
        self.home_label = home_label

    def quote_supplier(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        postcode: str | None = None,
        prefer_browser: bool = False,
    ) -> QuoteResult:
        with get_connector_for_supplier(supplier, prefer_browser=prefer_browser) as connector:
            result = connector.quote(
                supplier=supplier,
                quantity_liters=quantity_liters,
                context={
                    "postcode": postcode,
                    "home_label": self.home_label,
                },
            )
        result.currency = self.currency
        return result

