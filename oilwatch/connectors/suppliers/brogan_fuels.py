"""Brogan Fuels connector - redirects to Scottish Fuels."""

from __future__ import annotations

from typing import Any

import httpx

from oilwatch.connectors.base import BaseConnector
from oilwatch.models import OrderResult, QuoteResult


class BroganFuelsConnector(BaseConnector):
    """
    Brogan Fuels connector.
    
    Brogan Fuels redirects to Scottish Fuels for ordering.
    Same company, simpler checkout process.
    
    Quote mechanism: Redirect to Scottish Fuels or phone contact
    """
    
    def __init__(self) -> None:
        self.client = httpx.Client(
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.5",
            },
            timeout=30.0,
        )
        self.base_url = "https://www.brogans.co.uk"
        self.phone = "0345 300 8844"
        self.email = "domestic@brogans.co.uk"
        self.redirects_to = "Scottish Fuels (same company)"
    
    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        """
        Get a quote from Brogan Fuels.
        
        Strategy: Provide structured contact information.
        Brogan Fuels uses Scottish Fuels infrastructure.
        """
        postcode = context.get("postcode", "")
        
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="manual_action_required",
            source="brogan_fuels_manual",
            notes=self._build_quote_instructions(quantity_liters, postcode),
            raw_payload={
                "phone": self.phone,
                "email": self.email,
                "website": self.base_url,
                "redirects_to": self.redirects_to,
            },
        )
    
    def _build_quote_instructions(self, quantity_liters: int, postcode: str) -> str:
        """Build detailed instructions for obtaining a quote."""
        return (
            f"BROGAN FUELS - Quote Request for {quantity_liters}L Heating Oil\n\n"
            f"NOTE: Brogan Fuels is part of Scottish Fuels (same company)\n\n"
            f"CONTACT OPTIONS:\n"
            f"1. Phone: {self.phone}\n"
            f"2. Email: {self.email}\n"
            f"3. Website: {self.base_url}\n"
            f"4. Get quote via Scottish Fuels website\n\n"
            f"INFORMATION TO PROVIDE:\n"
            f"- Fuel type: Heating Oil\n"
            f"- Quantity: {quantity_liters} litres\n"
            f"- Postcode: {postcode or 'Your postcode'}\n"
            f"- Delivery address\n"
            f"- Contact details\n\n"
            f"NOTES:\n"
            f"- Same company as Scottish Fuels, simpler checkout\n"
            f"- Carbon offset option available\n"
            f"- Payment plans available - get price beforehand\n"
            f"- Call for minimum order volumes"
        )
    
    def place_order(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        agreed_price_per_liter: float,
        context: dict[str, Any],
    ) -> OrderResult:
        """Brogan Fuels orders via Scottish Fuels system."""
        return OrderResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            created_at=self.now(),
            quantity_liters=quantity_liters,
            agreed_price_per_liter=agreed_price_per_liter,
            status="manual_action_required",
            notes=f"Order via Scottish Fuels. Call {self.phone} or email {self.email}. Agreed price: £{agreed_price_per_liter}/L",
        )
