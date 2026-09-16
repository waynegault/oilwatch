"""ValueOils connector - regional-page prices, no browser.

The Aberdeenshire regional page renders a live price table server-side::

    Live Heating Oil Prices in Aberdeenshire
      | 500 Litres | 900 Litres
      Price Per Litre Ex VAT | 112.10p | 112.10p
      Total You Pay          | £604.53 | £1,075.35

The ``Total You Pay`` is the figure that compares with other suppliers: as the
page states elsewhere, it "includes vat, commission and any chargeable delivery
option selected", whereas the ppl excludes VAT and any commission. The connector
therefore reads the Total You Pay for the 900L tier (the tier our orders sit in,
900L+) and divides by 900, instead of uplifting the ppl by 5% and dropping the
commission.

URL: https://www.valueoils.com/regions/scotland/aberdeenshire/
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from oilwatch.connectors.base import BaseConnector
from oilwatch.http import build_client
from oilwatch.models import QuoteResult
from oilwatch.pricing import inclusive_total

#: The tier the regional page prices nearest our 900L+ orders.
_TIER_LITRES = 900

#: The 900L column of the "Total You Pay" row in the *heating oil* table (the
#: page also carries a gas oil table, so the match is anchored on the heading).
_TIER_TOTAL_RE = (
    r"Live\s*Heating\s*Oil\s*Prices[\s\S]{0,800}?Total\s*You\s*Pay"
    r"[\s\S]{0,120}?£\s*[\d,]+\.\d{2}"      # the 500L column
    r"[\s\S]{0,80}?£\s*([\d,]+\.\d{2})"     # the 900L column
)


class ValueOilsConnector(BaseConnector):
    """ValueOils' live regional price, inclusive of VAT and commission."""

    def __init__(self) -> None:
        # Shared client: timeouts, browser headers and transport-level retries
        # (see oilwatch/http.py, which also owns the default User-Agent).
        self.client = build_client(
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.5",
            }
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
        """Fetch the regional page and read the 900L "Total You Pay"."""
        postcode = context.get("postcode", "")

        try:
            response = self.client.get(self.regional_url)
            response.raise_for_status()
            tier_total = self._extract_tier_total(response.text)

            if tier_total is not None:
                price_per_liter = round(tier_total / _TIER_LITRES, 4)
                return QuoteResult(
                    supplier_id=int(supplier["id"]),
                    supplier_name=supplier["name"],
                    observed_at=self.now(),
                    quantity_liters=quantity_liters,
                    status="ok",
                    price_per_liter=price_per_liter,
                    total_price=inclusive_total(price_per_liter, quantity_liters),
                    source="valueoils_auto",
                    notes=(
                        f"900L Standard total from the ValueOils regional page "
                        f"(£{tier_total:.2f} inc VAT, incl. commission) = "
                        f"£{price_per_liter:.4f}/L. Postcode: {postcode or 'Not provided'}"
                    ),
                    raw_payload={
                        "url": self.regional_url,
                        "tier_litres": _TIER_LITRES,
                        "tier_total": tier_total,
                    },
                )

            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="manual_action_required",
                source="valueoils_auto",
                notes=f"Could not extract the Standard total. Call: 03300 570 857 or use Quick Quote at: {self.regional_url}",
            )

        except httpx.HTTPError as e:
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="error",
                source="valueoils_auto",
                notes=f"HTTP error fetching quote: {e!s}",
            )
        except Exception as e:  # noqa: BLE001 - reported as an error quote rather than raised
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="error",
                source="valueoils_auto",
                notes=f"Error fetching quote: {e!s}",
            )

    @staticmethod
    def _extract_tier_total(html: str) -> float | None:
        """The 900L "Total You Pay" (GBP, inc VAT and commission), or None."""
        match = re.search(_TIER_TOTAL_RE, html, re.IGNORECASE | re.DOTALL)
        if not match:
            return None
        return float(match.group(1).replace(",", ""))
