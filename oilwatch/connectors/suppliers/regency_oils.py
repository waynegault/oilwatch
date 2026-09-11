"""Regency Oils connector - instant quote system."""

from __future__ import annotations

from typing import Any

from oilwatch.connectors.base import BaseConnector
from oilwatch.models import QuoteResult


class RegencyOilsConnector(BaseConnector):
    """
    Regency Oils connector.
    
    This supplier advertises "free, instant no obligation quote".
    Also offers automatic top-up service and budget plans.
    
    Quote mechanism: Click-through quote system or phone contact
    """
    
    def __init__(self) -> None:
        # Manual contact only: there is no price page to fetch, so no HTTP client
        # is built. An unused one here would just leak a connection pool, since
        # the registry builds a connector per call.
        self.base_url = "https://www.regencyoils.com"
        self.phone = "0800 838500"
        self.services = [
            "Monthly budget plan",
            "Automatic top-up service", 
            "Community buying group service",
        ]
    
    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        """
        Get a quote from Regency Oils.
        
        Strategy: Provide structured contact information.
        The instant quote system requires interactive completion.
        """
        postcode = context.get("postcode", "")
        
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="manual_action_required",
            source="regency_oils_manual",
            notes=self._build_quote_instructions(quantity_liters, postcode),
            raw_payload={
                "phone": self.phone,
                "website": self.base_url,
                "services": self.services,
            },
        )
    
    def _build_quote_instructions(self, quantity_liters: int, postcode: str) -> str:
        """Build detailed instructions for obtaining a quote."""
        return (
            f"REGENCY OILS LTD - Quote Request for {quantity_liters}L Heating Oil\n\n"
            f"CONTACT OPTIONS:\n"
            f"1. Phone: {self.phone}\n"
            f"2. Website: {self.base_url}\n"
            f"3. Click 'Click here for a free, instant no obligation quote'\n\n"
            f"INFORMATION TO PROVIDE:\n"
            f"- Fuel type: Heating Oil\n"
            f"- Quantity: {quantity_liters} litres\n"
            f"- Postcode: {postcode or 'Your postcode'}\n"
            f"- Delivery address\n"
            f"- Contact details\n\n"
            f"ADDITIONAL SERVICES:\n"
            f"- Monthly budget plan available\n"
            f"- Automatic top-up service (never run out)\n"
            f"- Community buying group service (bulk discounts)\n\n"
            f"NOTES:\n"
            f"- Supplies: Heating oil, red diesel, diesel, HVO, petrol\n"
            f"- Also offers: Fuel additives, fuel tanks, fuel cards, lubricants\n"
            f"- Quote system: 'Instant no obligation quote'"
        )
    
