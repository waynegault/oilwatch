"""Oilfast Insch connector - enquiry form submission."""

from __future__ import annotations

from typing import Any

import httpx

from oilwatch.connectors.base import BaseConnector
from oilwatch.http import build_client
from oilwatch.models import OrderResult, QuoteResult


class OilfastConnector(BaseConnector):
    """
    Oilfast Insch connector.
    
    This supplier uses an enquiry form (no instant pricing).
    Requires: Full Name, Phone, Email, Postcode, Address, Message
    Response time: Usually within 24 hours
    
    Quote mechanism: Form submission via HubSpot or similar CRM
    """
    
    def __init__(self) -> None:
        # Shared client: timeouts, browser headers and transport-level retries
        # (see oilwatch/http.py, which also owns the default User-Agent).
        self.client = build_client(
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.5",
            }
        )
        self.base_url = "https://oilfast.co.uk"
        self.enquiry_url = "https://oilfast.co.uk/depot/insch/"
        self.phone = "01464 635999"
        self.general_phone = "03302 320 104"
        self.email = "insch@oilfast.co.uk"
    
    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        """
        Get a quote from Oilfast Insch.
        
        Strategy: This supplier requires manual contact.
        Return structured information for manual quote request.
        """
        postcode = context.get("postcode", "")
        
        # Return manual action required with full contact details
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="manual_action_required",
            source="oilfast_manual",
            notes=self._build_quote_instructions(quantity_liters, postcode),
            raw_payload={
                "depot": "Insch",
                "phone": self.phone,
                "general_phone": self.general_phone,
                "email": self.email,
                "enquiry_url": self.enquiry_url,
            },
        )
    
    def _build_quote_instructions(self, quantity_liters: int, postcode: str) -> str:
        """Build detailed instructions for obtaining a quote."""
        return (
            f"OILFAST INSCH - Quote Request for {quantity_liters}L Heating Oil\n\n"
            f"CONTACT OPTIONS:\n"
            f"1. Phone (Insch Depot): {self.phone}\n"
            f"2. Phone (General): {self.general_phone}\n"
            f"3. Email: {self.email}\n"
            f"4. Online Enquiry: {self.enquiry_url}\n\n"
            f"INFORMATION TO PROVIDE:\n"
            f"- Fuel type: Heating Oil (Kerosene)\n"
            f"- Quantity: {quantity_liters} litres\n"
            f"- Postcode: {postcode or 'Your postcode'}\n"
            f"- Address: Your full delivery address\n"
            f"- Contact name and phone number\n\n"
            f"NOTES:\n"
            f"- Local Aberdeenshire supplier (Insch depot)\n"
            f"- Also supplies: Bulk fuels, lubricants, AdBlue, HVO\n"
            f"- Response time: Usually within 24 hours\n"
            f"- Opening hours: Mon-Fri, check website for current hours"
        )
    
    def place_order(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        agreed_price_per_liter: float,
        context: dict[str, Any],
    ) -> OrderResult:
        """Oilfast requires manual order placement via phone or email."""
        return OrderResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            created_at=self.now(),
            quantity_liters=quantity_liters,
            agreed_price_per_liter=agreed_price_per_liter,
            status="manual_action_required",
            notes=f"Call {self.phone} (Insch) or {self.general_phone} to place order. Agreed price: £{agreed_price_per_liter}/L",
        )
