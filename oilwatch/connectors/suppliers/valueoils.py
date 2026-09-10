"""ValueOils connector - has instant pricing via Quick Quote form."""

from __future__ import annotations

import re
from typing import Any

import httpx

from oilwatch.connectors.base import BaseConnector
from oilwatch.models import OrderResult, QuoteResult
from oilwatch.pricing import DOMESTIC_VAT_RATE, apply_vat, inclusive_total, pence_to_pounds


class ValueOilsConnector(BaseConnector):
    """
    ValueOils connector.

    The Aberdeenshire regional page exposes server-rendered live prices in a
    table ("Live Heating Oil Prices ... Price Per Litre Ex VAT ..."). Those are
    ex-VAT pence-per-litre figures, which this connector normalises to inclusive
    of 5% domestic VAT.

    URL: https://www.valueoils.com/regions/scotland/aberdeenshire/
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
        self.base_url = "https://www.valueoils.com"
        self.quote_url = "https://www.valueoils.com/Quote.aspx"
        self.regional_url = "https://www.valueoils.com/regions/scotland/aberdeenshire/"
    
    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        """
        Get a quote from ValueOils.

        Strategy: Fetch the Aberdeenshire regional page, which renders the live
        heating-oil price table server-side, and extract the ex-VAT pence price.
        """
        postcode = context.get("postcode", "")

        try:
            # The regional page is server-rendered; the /Quote.aspx form is
            # JavaScript-heavy and returns an error page to plain HTTP clients.
            response = self.client.get(self.regional_url)
            response.raise_for_status()

            # Try to extract price from page (ex-VAT pence)
            ex_vat_price = self._extract_price(response.text, quantity_liters)

            if ex_vat_price is not None:
                # Stored price_per_liter is inclusive of 5% domestic VAT.
                price_per_liter = apply_vat(ex_vat_price, DOMESTIC_VAT_RATE)
                total_price = inclusive_total(price_per_liter, quantity_liters)

                return QuoteResult(
                    supplier_id=int(supplier["id"]),
                    supplier_name=supplier["name"],
                    observed_at=self.now(),
                    quantity_liters=quantity_liters,
                    status="ok",
                    price_per_liter=price_per_liter,
                    total_price=total_price,
                    source="valueoils_auto",
                    notes=f"Price extracted from Quick Quote for {quantity_liters}L (Ex VAT: £{ex_vat_price:.4f}/L, Inc VAT: £{price_per_liter:.4f}/L). Postcode: {postcode or 'Not provided'}",
                    raw_payload={
                        "url": self.regional_url,
                        "quantity_tier": "900L+" if quantity_liters >= 900 else "500L",
                        "price_ex_vat": ex_vat_price,
                    },
                )
            else:
                # Fallback to manual action
                return QuoteResult(
                    supplier_id=int(supplier["id"]),
                    supplier_name=supplier["name"],
                    observed_at=self.now(),
                    quantity_liters=quantity_liters,
                    status="manual_action_required",
                    source="valueoils_auto",
                    notes=f"Could not extract automated price. Call: 03300 570 857 or use Quick Quote at: {self.regional_url}",
                )
                
        except httpx.HTTPError as e:
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="error",
                source="valueoils_auto",
                notes=f"HTTP error fetching quote: {str(e)}",
            )
        except Exception as e:
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="error",
                source="valueoils_auto",
                notes=f"Error fetching quote: {str(e)}",
            )
    
    def _extract_price(self, html: str, quantity_liters: int) -> float | None:
        """Extract the ex-VAT heating-oil price per litre (GBP) from the page.

        The regional page renders a table like::

            <h3>Live Heating Oil Prices in Aberdeenshire</h3>
            <table>...
              <tr><td><strong>Price Per Litre Ex VAT</strong></td>
                  <td align="right">103.90p</td><td align="right">103.90p</td></tr>
            </table>

        ``quantity_liters`` is accepted for API compatibility; the heating-oil
        tiers are currently priced identically at 500L and 900L.
        """
        # Anchor on the "Live Heating Oil Prices" heading so we do not pick up
        # the adjacent "Live Gas Oil Prices" table.
        primary = (
            r"Live\s*Heating\s*Oil\s*Prices"
            r".*?Price\s*Per\s*Litre\s*Ex\s*VAT"
            r".*?(\d{2,3}(?:\.\d{1,2})?)\s*p"
        )
        match = re.search(primary, html, re.IGNORECASE | re.DOTALL)
        if match:
            return pence_to_pounds(match.group(1))

        # Fallback: any "Price Per Litre Ex VAT ... NNNp" sequence.
        fallback = r"Price\s*Per\s*Litre\s*Ex\s*VAT.*?(\d{2,3}(?:\.\d{1,2})?)\s*p"
        match = re.search(fallback, html, re.IGNORECASE | re.DOTALL)
        if match:
            return pence_to_pounds(match.group(1))

        return None
    
    def place_order(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        agreed_price_per_liter: float,
        context: dict[str, Any],
    ) -> OrderResult:
        """ValueOils requires order via Quick Quote form or phone."""
        return OrderResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            created_at=self.now(),
            quantity_liters=quantity_liters,
            agreed_price_per_liter=agreed_price_per_liter,
            status="manual_action_required",
            notes="Order via Quick Quote form at: https://www.valueoils.com or call: 03300 570 857 (Mon-Fri 9am-5pm)",
        )
