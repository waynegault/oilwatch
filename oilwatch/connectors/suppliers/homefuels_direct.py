"""HomeFuels Direct connector - has live pricing API."""

from __future__ import annotations

import re
from typing import Any

import httpx

from oilwatch.connectors.base import BaseConnector
from oilwatch.http import build_client
from oilwatch.models import QuoteResult
from oilwatch.pricing import (
    DOMESTIC_VAT_RATE,
    apply_vat,
    inclusive_total,
    normalise_price_per_litre,
    pence_to_pounds,
)


class HomeFuelsDirectConnector(BaseConnector):
    """
    HomeFuels Direct connector.
    
    This supplier displays live prices on their website:
    - UK Average: ~132 pence/litre (for orders over 900 litres)
    - Shows prices for 500L and 900L tiers
    - Requires postcode for accurate pricing
    
    Quote mechanism: Scrape live price from homepage or price page
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
        self.base_url = "https://homefuelsdirect.co.uk"
        self.price_url = "https://homefuelsdirect.co.uk/home/heating-oil-prices"
        self.aberdeenshire_url = "https://homefuelsdirect.co.uk/home/heating-oil-prices/aberdeenshire"
    
    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        """
        Get a quote from HomeFuels Direct.
        
        Strategy: Fetch the price page and extract current UK average pricing.
        HomeFuels shows live prices updated throughout the day.
        """
        postcode = context.get("postcode", "")
        
        try:
            ex_vat_price, source_url = self._get_live_price()

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
                    source="homefuels_live_price",
                    notes=f"Live price extracted from {source_url} for {quantity_liters}L (Ex VAT: £{ex_vat_price:.4f}/L, Inc VAT: £{price_per_liter:.4f}/L). Postcode: {postcode or 'Not provided'}",
                    raw_payload={
                        "url": source_url,
                        "quantity_tier": "900L+" if quantity_liters >= 900 else "500L",
                        "price_type": "live_uk_average",
                    },
                )

            # No parseable price found - be honest rather than inventing a stale figure.
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="manual_action_required",
                source="homefuels_direct_auto",
                notes=f"Could not extract a live price (site is JavaScript-rendered). Contact: enquiries@homefuelsdirect.co.uk. Page: {self.aberdeenshire_url}",
            )
                
        except httpx.HTTPError as e:
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="error",
                source="homefuels_direct_auto",
                notes=f"HTTP error fetching quote: {str(e)}",
            )
        except Exception as e:
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="error",
                source="homefuels_direct_auto",
                notes=f"Error fetching quote: {str(e)}",
            )
    
    def _get_live_price(self) -> tuple[float | None, str]:
        """Attempt to scrape the current ex-VAT price in GBP per litre.

        The page is server-rendered and exposes the price in a
        ``<span id="currentLivePrice">99.15</span> pence / litre`` element. We
        try the regional page first (more specific) and fall back to the
        national price page. Returns ``(price, url)`` where ``price`` is
        ``None`` when nothing parseable was found (callers must then fall back
        to a manual quote rather than reporting a fabricated figure).
        """
        # Prefer the dedicated price span, then a generic "NN pence" wording.
        price_patterns = [
            r'id=["\']?currentLivePrice["\']?[^>]*>\s*(\d+(?:\.\d{1,2})?)\s*<',
            r"(\d{2,3}(?:\.\d{1,2})?)\s*pence",
        ]
        # pounds-per-litre patterns, e.g. "£1.32 per litre"
        pound_patterns = [r"£?(\d\.\d{2})\s*(?:per\s*litre|/l)"]

        # A transport failure on one page is worth retrying on the other, but if
        # neither answers the caller has to hear about it: reporting a dead site
        # as "no price on the page" is a different, and misleading, problem.
        transport_error: httpx.HTTPError | None = None
        for url in (self.aberdeenshire_url, self.price_url):
            try:
                response = self.client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                transport_error = exc
                continue

            for pattern in price_patterns:
                match = re.search(pattern, response.text, re.IGNORECASE)
                if match:
                    # These patterns match pence, so convert explicitly.
                    price = pence_to_pounds(match.group(1))
                    return price, url

            for pattern in pound_patterns:
                match = re.search(pattern, response.text, re.IGNORECASE)
                if match:
                    price = normalise_price_per_litre(match.group(1))
                    if price is not None:
                        return price, url

        if transport_error is not None:
            raise transport_error

        return None, self.price_url
    
