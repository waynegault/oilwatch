"""Rix connector - quote tool and depot contact."""

from __future__ import annotations

from typing import Any

from oilwatch.connectors.base import BaseConnector
from oilwatch.models import OrderResult, QuoteResult


class RixConnector(BaseConnector):
    """
    Rix connector.
    
    Rix has a "Get your fuel quote here" online tool.
    Aberdeen depot specific contact available.
    
    Quote mechanism: Online quote tool or direct depot contact
    """
    
    def __init__(self) -> None:
        # Manual contact only: there is no price page to fetch, so no HTTP client
        # is built. An unused one here would just leak a connection pool, since
        # the registry builds a connector per call.
        self.base_url = "https://www.rix.co.uk"
        self.aberdeen_url = "https://www.rix.co.uk/locations/aberdeen-depot"
        self.quote_url = "https://www.rix.co.uk/fuels/heating-oil"  # Main heating oil page
        self.aberdeen_phone = "01224 455477"
        self.general_phone = "0800 542 4207"
        self.aberdeen_email = "montsales@rix.co.uk"
        self.general_email = "sales@rix.co.uk"
    
    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        """
        Get a quote from Rix.
        
        Strategy: Provide structured contact information for quote request.
        The online quote tool requires interactive form completion.
        """
        postcode = context.get("postcode", "")
        
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="manual_action_required",
            source="rix_manual",
            notes=self._build_quote_instructions(quantity_liters, postcode),
            raw_payload={
                "depot": "Aberdeen",
                "aberdeen_phone": self.aberdeen_phone,
                "general_phone": self.general_phone,
                "aberdeen_email": self.aberdeen_email,
                "quote_url": self.quote_url,
                "depot_url": self.aberdeen_url,
            },
        )
    
    def _build_quote_instructions(self, quantity_liters: int, postcode: str) -> str:
        """Build detailed instructions for obtaining a quote."""
        return (
            f"RIX PETROLEUM - Quote Request for {quantity_liters}L Heating Oil\n\n"
            f"CONTACT OPTIONS:\n"
            f"1. Aberdeen Depot: {self.aberdeen_phone}\n"
            f"2. General: {self.general_phone}\n"
            f"3. Aberdeen Email: {self.aberdeen_email}\n"
            f"4. General Email: {self.general_email}\n"
            f"5. Online Quote: {self.quote_url}\n\n"
            f"INFORMATION TO PROVIDE:\n"
            f"- Fuel type: Heating Oil (Kerosene)\n"
            f"- Quantity: {quantity_liters} litres\n"
            f"- Postcode: {postcode or 'Your postcode'}\n"
            f"- Delivery address in Aberdeenshire\n"
            f"- Contact details\n\n"
            f"NOTES:\n"
            f"- Local Aberdeen depot serves Peterhead, Ellon, Laurencekirk\n"
            f"- Part of Scotland's trusted heating oil people\n"
            f"- Over 30,000 homes supplied across Scotland\n"
            f"- Online quote tool: 'Get your fuel quote here'"
        )
    
    def place_order(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        agreed_price_per_liter: float,
        context: dict[str, Any],
    ) -> OrderResult:
        """Rix requires manual order placement."""
        return OrderResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            created_at=self.now(),
            quantity_liters=quantity_liters,
            agreed_price_per_liter=agreed_price_per_liter,
            status="manual_action_required",
            notes=f"Call Aberdeen depot: {self.aberdeen_phone} or use online quote tool. Agreed price: £{agreed_price_per_liter}/L",
        )
