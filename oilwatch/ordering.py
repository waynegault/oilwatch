from __future__ import annotations

from oilwatch.connectors import get_connector_for_supplier
from oilwatch.models import OrderResult


class OrderService:
    def place_order(
        self,
        supplier: dict,
        quantity_liters: int,
        agreed_price_per_liter: float,
        postcode: str | None = None,
        home_label: str | None = None,
    ) -> OrderResult:
        with get_connector_for_supplier(supplier) as connector:
            return connector.place_order(
                supplier=supplier,
                quantity_liters=quantity_liters,
                agreed_price_per_liter=agreed_price_per_liter,
                context={
                    "postcode": postcode,
                    "home_label": home_label or "",
                },
            )

