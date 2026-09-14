"""HomeFuels Direct browser connector with auto-login and API discovery."""

from __future__ import annotations

import re
from typing import Any

from playwright.async_api import Page

from oilwatch.connectors.browser_base import BrowserConnector
from oilwatch.identity import load_contact
from oilwatch.logging_setup import get_logger
from oilwatch.models import QuoteResult
from oilwatch.pricing import apply_vat, normalise_price_per_litre, pence_to_pounds

log = get_logger("connectors.homefuels_direct")

#: The quote form's postcode control, shared by the readiness wait and the fill.
_POSTCODE_SELECTOR = (
    'input[name="postcode"], input[id*="postcode"], '
    'input[placeholder*="postcode"], input[autocomplete="postal-code"]'
)

#: The page's "NN pence per litre" wording, shared by the browser scan and the
#: HTTP fallback so the two cannot drift apart.
_PENCE_PER_LITRE = r'(\d{2,3})\s*pence\s*per\s*litre'


class HomeFuelsDirectBrowserConnector(BrowserConnector):
    """
    HomeFuels Direct browser connector.
    
    HomeFuels Direct has an instant quote form showing live prices.
    This connector:
    1. Navigates to the quote page
    2. Fills in the quote form (postcode, quantity)
    3. Extracts the price from the response
    4. Discovers any underlying API endpoints
    
    Note: HomeFuels Direct may not require login for basic quotes.
    """
    
    def __init__(self) -> None:
        super().__init__()
        self.supplier_key = "homefuels_direct"
        self.supplier_name = "HomeFuels Direct"
        self.base_url = "https://homefuelsdirect.co.uk"
        self.login_url = "https://homefuelsdirect.co.uk/my-account/"
        self.quote_url = "https://homefuelsdirect.co.uk/home/heating-oil-prices/aberdeenshire"
        self._requires_login = False
        self._discovered_api: str | None = None
    
    async def login(self, page: Page, email: str, password: str) -> bool:
        """Optional sign-in; HomeFuels Direct quotes work signed-out (see the base)."""
        return await self._optional_login(page, email, password)
    
    async def get_quote_with_browser(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
        page: Page,
    ) -> QuoteResult:
        """
        Get a quote from HomeFuels Direct using browser automation.
        
        Strategy:
        1. Navigate to quote page
        2. Fill in quote form (postcode, quantity)
        3. Click "Get Live Prices" or "Start Your Order"
        4. Extract price from response
        """
        try:
            postcode = context.get("postcode", "") or load_contact().postcode
            
            # Navigate to quote page
            await page.goto(self.quote_url, wait_until="domcontentloaded")
            # Wait for the form rather than sleeping a flat 3s: the postcode
            # control is the readiness signal, and the wait is bounded.
            await self._wait_for(
                page,
                lambda: page.query_selector(_POSTCODE_SELECTOR),
                what="the HomeFuels quote form",
            )

            # Find and fill quote form fields
            # Postcode field
            postcode_field = await page.query_selector(_POSTCODE_SELECTOR)
            
            # Quantity field
            quantity_field = await page.query_selector(
                'input[name="quantity"], input[id*="quantity"], '
                'input[type="number"], input[placeholder*="litres"], '
                'input[placeholder*="quantity"]'
            )
            
            # Get prices button
            price_button = await page.query_selector(
                'button:has-text("Price"), button:has-text("Quote"), '
                'button:has-text("Order"), input[value*="Start"], '
                'button:has-text("Live")'
            )
            
            # Fill in fields
            if postcode_field:
                await postcode_field.fill(postcode)
            
            if quantity_field:
                await quantity_field.fill(str(quantity_liters))
            
            # Submit form
            if price_button:
                await price_button.click()

                # Wait for the price to appear rather than sleeping a flat 5s;
                # the extraction below re-reads it once it is there.
                await self._wait_for(
                    page,
                    lambda: self._price_ready(page, quantity_liters),
                    what="a HomeFuels price",
                )

            # Try to extract price from page. HomeFuels quotes the price ex-VAT
            # (the HTTP connector reads the same page on that basis), so it is
            # brought onto the app-wide inclusive-of-5% basis before storage.
            ex_vat_price = await self._extract_price(page, quantity_liters)

            if ex_vat_price:
                return self._quote_from_ex_vat(
                    supplier,
                    quantity_liters,
                    ex_vat_price,
                    source="homefuels_direct_browser",
                    notes=(
                        f"Price extracted via browser automation for {quantity_liters}L "
                        f"(Ex VAT: £{ex_vat_price:.4f}/L, Inc VAT: £{apply_vat(ex_vat_price):.4f}/L)."
                    ),
                    raw_payload={
                        "quote_url": self.quote_url,
                        "postcode": postcode,
                        "method": "browser_automation",
                        "api_endpoint": self._discovered_api or "N/A",
                    },
                )
            
            # Fall back to HTTP scraping
            return await self._fallback_to_http(supplier, quantity_liters, context)

        except Exception as e:  # noqa: BLE001 - any browser failure degrades to the HTTP fallback
            # Fall back to HTTP scraping
            return await self._fallback_to_http(supplier, quantity_liters, context, str(e))

    async def _price_ready(self, page: Page, quantity_liters: int) -> bool:
        """True once a price can be read off the page, for the bounded wait."""
        return await self._extract_price(page, quantity_liters) is not None

    async def _extract_price(self, page: Page, quantity_liters: int) -> float | None:
        """Extract price per litre from the page."""
        try:
            content = await page.content()
            
            # HomeFuels shows prices like "138 pence per litre" or "£X.XX".
            # A "£NNN.NN (Inc VAT)" total used to be accepted here too, but it
            # is a basket total, not a per-litre price, and running it through
            # the per-litre normaliser only invented a figure.
            patterns = [
                _PENCE_PER_LITRE,
                r'(\d{2,3})\s*p\s*/\s*l',
                r'£?(\d+\.\d{2})\s*per\s*litre',
                r'total.*?£?(\d+\.\d{2})',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    price = normalise_price_per_litre(match.group(1))
                    if price is not None:
                        return price

            # Try to find price in specific elements
            price_elements = await page.query_selector_all(
                '.price, .total, .quote-price, [class*="price"], [id*="price"]'
            )

            for element in price_elements:
                text = await element.text_content()
                if text:
                    match = re.search(r'(\d+\.\d{2})', text)
                    if match:
                        price = normalise_price_per_litre(match.group(1))
                        if price is not None:
                            return price

            return None

        except Exception as e:  # noqa: BLE001 - a page without a readable price is "no price"
            log.debug("price extraction failed: %s", e)
            return None

    async def _fallback_to_http(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
        error: str = "",
    ) -> QuoteResult:
        """Fall back to HTTP scraping if browser automation fails."""
        import httpx
        
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                response = await client.get(self.quote_url)
                response.raise_for_status()
                content = response.text
            
            # Extract price using regex
            patterns = [
                _PENCE_PER_LITRE,
                r'UK\s*Average.*?(\d{2,3})\s*pence',
            ]

            for pattern in patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    # The page's pence figure is ex-VAT, like the browser path.
                    price_pence = int(match.group(1))
                    return self._quote_from_ex_vat(
                        supplier,
                        quantity_liters,
                        pence_to_pounds(price_pence),
                        source="homefuels_direct_http_fallback",
                        notes=(
                            f"Price extracted via HTTP fallback (browser: {error or 'N/A'}). "
                            f"UK Average: {price_pence:.2f}p/L ex VAT"
                        ),
                    )
            
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="manual_action_required",
                source="homefuels_direct_browser",
                notes=f"Could not extract automated price. Contact: enquiries@homefuelsdirect.co.uk. Browser error: {error}",
            )

        except Exception as e:  # noqa: BLE001 - reported as an error quote rather than raised
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="error",
                source="homefuels_direct_browser",
                notes=f"HTTP fallback failed: {str(e)}",
            )
