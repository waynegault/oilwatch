"""Telephone quote script tool for manual quote collection."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class TelephoneQuoteScript:
    """
    Telephone Quote Script Tool.
    
    Generates structured scripts and tracking sheets for calling suppliers
    to obtain heating oil quotes manually.
    """
    
    def __init__(self) -> None:
        self.quantity_liters = 1000
        self.postcode = ""
        self.address = ""
        self.contact_name = ""
    
    def configure(
        self,
        quantity_liters: int = 1000,
        postcode: str = "",
        address: str = "",
        contact_name: str = "",
    ) -> None:
        """Configure the quote script with customer details."""
        self.quantity_liters = quantity_liters
        self.postcode = postcode
        self.address = address
        self.contact_name = contact_name
    
    def generate_script(self, supplier: dict[str, Any]) -> str:
        """Generate a telephone script for a specific supplier."""
        supplier_name = supplier.get("name", "Supplier")
        phone = supplier.get("phone", "Not available")
        email = supplier.get("email", "Not available")
        website = supplier.get("website", "")
        notes = supplier.get("notes", "")
        
        script = f"""
================================================================================
TELEPHONE QUOTE SCRIPT - {supplier_name.upper()}
================================================================================

SUPPLIER DETAILS:
  Phone: {phone}
  Email: {email}
  Website: {website}

--------------------------------------------------------------------------------
CALL SCRIPT
--------------------------------------------------------------------------------

YOU: "Hello, I'd like to get a quote for heating oil delivery please."

THEM: [Will ask for details]

YOU: "I need {self.quantity_liters} litres of kerosene (heating oil) for domestic use."

THEM: [May ask for postcode]

YOU: "My delivery postcode is: {self.postcode or '[YOUR POSTCODE]'}"

THEM: [May ask for address]

YOU: "The delivery address is: {self.address or '[YOUR FULL ADDRESS]'}"

THEM: [May ask for contact name]

YOU: "My name is: {self.contact_name or '[YOUR NAME]'}"

--------------------------------------------------------------------------------
QUESTIONS TO ASK:
--------------------------------------------------------------------------------

1. "What is the price per litre for {self.quantity_liters} litres?"
   → Write down: £_______ per litre (or _______ pence per litre)

2. "Is VAT included in that price?"
   → Domestic heating oil is 5% VAT
   → Write down: Price £_______ (Inc VAT) or £_______ (Ex VAT)

3. "What is the total cost for {self.quantity_liters} litres?"
   → Write down: £_______ total

4. "What are the available delivery dates?"
   → Write down: Earliest: _______________

5. "Is there a delivery charge?"
   → Write down: £_______ (or "Included")

6. "What are the payment terms?"
   → □ Pay on delivery  □ Pay in advance  □ Account terms

7. "Do you offer any discounts?"
   → □ First customer discount  □ Bulk discount  □ Budget plan

--------------------------------------------------------------------------------
ADDITIONAL NOTES FOR THIS SUPPLIER:
--------------------------------------------------------------------------------
{notes if notes else "No additional notes available."}

--------------------------------------------------------------------------------
QUOTE RECORDING
--------------------------------------------------------------------------------

After the call, record:
  □ Price per litre: £_______
  □ Total price: £_______
  □ VAT included: □ Yes  □ No
  □ Delivery date: _______________
  □ Delivery charge: £_______
  □ Payment terms: _______________
  □ Quote valid until: _______________
  □ Reference number: _______________

================================================================================
"""
        return script
    
    def generate_call_sheet(self, suppliers: list[dict[str, Any]]) -> str:
        """Generate a call sheet for multiple suppliers."""
        sheet = f"""
================================================================================
HEATING OIL QUOTE CALL SHEET
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
================================================================================

CUSTOMER DETAILS:
  Name: {self.contact_name or '[Your Name]'}
  Postcode: {self.postcode or '[Your Postcode]'}
  Address: {self.address or '[Your Address]'}
  Quantity: {self.quantity_liters} litres

================================================================================
SUPPLIERS TO CALL
================================================================================

"""
        for i, supplier in enumerate(suppliers, 1):
            sheet += f"""
{i}. {supplier.get('name', 'Unknown')}
   Phone: {supplier.get('phone', 'Not available')}
   Email: {supplier.get('email', 'Not available')}
   
   Quote Details:
   □ Price per litre: £_______ (or _______p)
   □ Total price: £_______
   □ VAT: □ Inc  □ Ex
   □ Delivery: £_______
   □ Date: _______________
   □ Notes: _______________________________
   
   Follow-up:
   □ Called  □ Voicemail  □ Callback scheduled
   □ Quote received  □ Not available

--------------------------------------------------------------------------------
"""
        
        sheet += """
================================================================================
SUMMARY TABLE
================================================================================

| Supplier | Price/L | Total | VAT | Delivery | Date | Best? |
|----------|---------|-------|-----|----------|------|-------|
|          | £       | £     |     | £        |      |       |
|          | £       | £     |     | £        |      |       |
|          | £       | £     |     | £        |      |       |
|          | £       | £     |     | £        |      |       |
|          | £       | £     |     | £        |      |       |

BEST QUOTE: _______________________________
Date: _______________

================================================================================
"""
        return sheet
    
    def export_to_json(
        self,
        suppliers: list[dict[str, Any]],
        output_path: Path | str,
    ) -> Path:
        """Export call sheet data to JSON for tracking."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        data = {
            "generated_at": datetime.now().isoformat(),
            "customer": {
                "name": self.contact_name,
                "postcode": self.postcode,
                "address": self.address,
                "quantity_liters": self.quantity_liters,
            },
            "suppliers": [
                {
                    "id": s.get("id"),
                    "name": s.get("name"),
                    "phone": s.get("phone"),
                    "email": s.get("email"),
                    "website": s.get("website"),
                    "quote_status": "pending",
                    "price_per_liter": None,
                    "total_price": None,
                    "vat_included": None,
                    "delivery_charge": None,
                    "delivery_date": None,
                    "notes": "",
                }
                for s in suppliers
            ],
        }
        
        output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return output_path
    
    def get_quick_reference(self, suppliers: list[dict[str, Any]]) -> str:
        """Generate a quick reference card for calling."""
        ref = f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                    HEATING OIL QUOTE - QUICK REFERENCE                       ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Quantity: {self.quantity_liters} litres                                                  ║
║  Postcode: {self.postcode or '[Your Postcode]':<60} ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  SUPPLIERS TO CALL:                                                          ║
╠══════════════════════════════════════════════════════════════════════════════╣
"""
        for supplier in suppliers:
            name = (supplier.get("name") or "Unknown")[:25].ljust(25)
            phone = supplier.get("phone") or "N/A"
            phone = phone[:20].ljust(20) if phone else "N/A".ljust(20)
            ref += f"║  ☐ {name} | {phone}                     ║\n"
        
        ref += """╚══════════════════════════════════════════════════════════════════════════════╝

TIP: Start with: "Hi, I need a quote for 1000 litres of heating oil please."
"""
        return ref
