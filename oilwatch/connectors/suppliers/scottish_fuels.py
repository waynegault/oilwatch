"""Scottish Fuels connector - requires account for quote generator."""

from __future__ import annotations

from typing import Any

from oilwatch.connectors.base import BaseConnector
from oilwatch.models import QuoteResult


class ScottishFuelsConnector(BaseConnector):
    """
    Scottish Fuels connector.
    
    This supplier requires online account registration to access
    the quote generator. No instant pricing without login.
    
    Quote mechanism: Account required for online quote generator
    """
    
    def __init__(self) -> None:
        # Manual contact only: there is no price page to fetch, so no HTTP client
        # is built. An unused one here would just leak a connection pool, since
        # the registry builds a connector per call.
        self.base_url = "https://scottishfuels.co.uk"
        self.quote_url = "https://scottishfuels.co.uk/heating-oil-in-aberdeenshire/"
        self.phone = "0345 300 8844"
        self.local_phone = "01224 213 132"
        self.email = "info@scottishfuels.co.uk"
        self.account_required = True
    
    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        """
        Get a quote from Scottish Fuels.
        
        Strategy: Provide structured contact information.
        Online quote generator requires account registration.
        """
        postcode = context.get("postcode", "")
        
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="manual_action_required",
            source="scottish_fuels_manual",
            notes=self._build_quote_instructions(quantity_liters, postcode),
            raw_payload={
                "phone": self.phone,
                "local_phone": self.local_phone,
                "website": self.base_url,
                "account_required": self.account_required,
            },
        )
    
    def _build_quote_instructions(self, quantity_liters: int, postcode: str) -> str:
        """Build detailed instructions for obtaining a quote."""
        return (
            f"SCOTTISH FUELS - Quote Request for {quantity_liters}L Heating Oil\n\n"
            f"CONTACT OPTIONS:\n"
            f"1. Phone (General): {self.phone}\n"
            f"2. Phone (Aberdeenshire): {self.local_phone}\n"
            f"3. Website: {self.base_url}\n\n"
            f"ONLINE QUOTE (ACCOUNT REQUIRED):\n"
            f"- Sign up for online account (takes a few minutes)\n"
            f"- Access online quote generator for instant quotes\n"
            f"- Track orders and re-order online\n\n"
            f"INFORMATION TO PROVIDE:\n"
            f"- Fuel type: Heating Oil\n"
            f"- Quantity: {quantity_liters} litres\n"
            f"- Postcode: {postcode or 'Your postcode'}\n"
            f"- Delivery address\n"
            f"- Contact details\n\n"
            f"NOTES:\n"
            f"- Prices fluctuate, no set price\n"
            f"- Minimum order volumes may apply - check when calling\n"
            f"- Carbon offset option available\n"
            f"- Order tracking available online"
        )
    
