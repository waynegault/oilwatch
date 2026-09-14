"""ValueOils browser connector with auto-login and API discovery."""

from __future__ import annotations

import re
from typing import Any

from playwright.async_api import Page

from oilwatch.connectors.browser_base import BrowserConnector
from oilwatch.identity import load_contact
from oilwatch.logging_setup import get_logger
from oilwatch.models import QuoteResult
from oilwatch.pricing import apply_vat, normalise_price_per_litre, pence_to_pounds

log = get_logger("connectors.valueoils")

#: The Quick Quote form's postcode control, shared by the readiness wait and
#: the fill.
_POSTCODE_SELECTOR = (
    'input[name="postcode"], input[id*="postcode"], input[placeholder*="postcode"]'
)


class ValueOilsBrowserConnector(BrowserConnector):
    """
    ValueOils browser connector.
    
    ValueOils has a Quick Quote form that shows instant pricing.
    This connector:
    1. Navigates to the quote page
    2. Fills in the Quick Quote form
    3. Extracts the price from the response
    4. Falls back to web scraping if browser automation fails
    
    Note: ValueOils may not require login for basic quotes.
    """
    
    def __init__(self) -> None:
        super().__init__()
        self.supplier_key = "valueoils"
        self.supplier_name = "ValueOils"
        self.base_url = "https://www.valueoils.com"
        self.login_url = "https://www.valueoils.com/my-account/"
        self.quote_url = "https://www.valueoils.com/regions/scotland/aberdeenshire/"
        self._requires_login = False
    
    async def login(self, page: Page, email: str, password: str) -> bool:
        """Optional sign-in; ValueOils quotes work signed-out (see the base)."""
        return await self._optional_login(page, email, password)
    
    async def get_quote_with_browser(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
        page: Page,
    ) -> QuoteResult:
        """
        Get a quote from ValueOils using browser automation.
        
        Strategy:
        1. Navigate to quote page
        2. Fill in Quick Quote form (usage, fuel type, postcode, email, quantity)
        3. Submit and extract price
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
                what="the ValueOils Quick Quote form",
            )

            # Find and fill Quick Quote form fields
            # Usage dropdown (Domestic/Commercial)
            usage_select = await page.query_selector(
                'select[name="usage_type"], select[id*="usage"], '
                'select[aria-label*="usage"]'
            )
            
            # Fuel type dropdown
            fuel_select = await page.query_selector(
                'select[name="fuel_type"], select[id*="fuel"], '
                'select[aria-label*="fuel"]'
            )
            
            # Postcode field
            postcode_field = await page.query_selector(_POSTCODE_SELECTOR)
            
            # Email field
            email_field = await page.query_selector(
                'input[name="email"], input[id*="email"], '
                'input[type="email"], input[placeholder*="email"]'
            )
            
            # Quantity field
            quantity_field = await page.query_selector(
                'input[name="quantity"], input[id*="quantity"], '
                'input[type="number"], input[placeholder*="quantity"]'
            )
            
            # Get quote button
            quote_button = await page.query_selector(
                'button:has-text("Quote"), button:has-text("Get Quote"), '
                'input[value*="Quote"], button:has-text("Get Price"), '
                'button:has-text("Start Order")'
            )
            
            # Fill in fields
            if usage_select:
                try:
                    await usage_select.select_option("domestic")
                except Exception as exc:  # noqa: BLE001
                    log.debug("could not select usage=domestic: %s", exc)

            if fuel_select:
                try:
                    await fuel_select.select_option("kerosene")
                except Exception as exc:  # noqa: BLE001
                    log.debug("could not select fuel=kerosene: %s", exc)
            
            if postcode_field:
                await postcode_field.fill(postcode)
            
            if email_field:
                await email_field.fill(load_contact().email)
            
            if quantity_field:
                await quantity_field.fill(str(quantity_liters))
            
            # Submit form
            if quote_button:
                await quote_button.click()

                # Wait for the price to appear rather than sleeping a flat 5s;
                # the extraction below re-reads it once it is there.
                await self._wait_for(
                    page,
                    lambda: self._price_ready(page, quantity_liters),
                    what="a ValueOils price",
                )

            # Try to extract price from page. ValueOils quotes ex-VAT (the HTTP
            # connector reads the same regional page on that basis), so it is
            # brought onto the app-wide inclusive-of-5% basis before storage.
            ex_vat_price = await self._extract_price(page, quantity_liters)

            if ex_vat_price:
                return self._quote_from_ex_vat(
                    supplier,
                    quantity_liters,
                    ex_vat_price,
                    source="valueoils_browser",
                    notes=(
                        f"Price extracted via browser automation for {quantity_liters}L "
                        f"(Ex VAT: £{ex_vat_price:.4f}/L, Inc VAT: £{apply_vat(ex_vat_price):.4f}/L)."
                    ),
                    raw_payload={
                        "quote_url": self.quote_url,
                        "postcode": postcode,
                        "method": "browser_automation",
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

            # ValueOils shows prices like "155.80p" for heating oil
            patterns = [
                r'Heating\s*Oil.*?Kerosene.*?(\d{2,3}\.\d{2})\s*p',
                r'Kerosene.*?(\d{2,3}\.\d{2})\s*p',
                r'(\d{2,3}\.\d{2})\s*p.*?litre',
                r'£?(\d+\.\d{2})\s*per\s*litre',
                r'total.*?£?(\d+\.\d{2})',
            ]

            for pattern in patterns:
                match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
                if match:
                    # normalise handles the pence/pounds distinction (>100 is
                    # pence), so the same rule applies as everywhere else.
                    return normalise_price_per_litre(match.group(1))

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
                r'Heating\s*Oil.*?Kerosene.*?(\d{2,3}\.\d{2})\s*p.*?900',
                r'900\s*Litres.*?(\d{2,3}\.\d{2})\s*p',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
                if match:
                    # The regional table is "Price Per Litre Ex VAT", like the
                    # browser path, so the same 5% uplift is applied.
                    ex_vat_price = pence_to_pounds(float(match.group(1)))
                    return self._quote_from_ex_vat(
                        supplier,
                        quantity_liters,
                        ex_vat_price,
                        source="valueoils_http_fallback",
                        notes=(
                            f"Price extracted via HTTP fallback (browser: {error or 'N/A'}). "
                            f"Ex VAT: £{ex_vat_price:.4f}/L, Inc VAT: £{apply_vat(ex_vat_price):.4f}/L"
                        ),
                    )
            
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="error",
                source="valueoils_browser",
                notes=f"Could not extract price. Browser error: {error}",
            )

        except Exception as e:  # noqa: BLE001 - reported as an error quote rather than raised
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="error",
                source="valueoils_browser",
                notes=f"HTTP fallback failed: {str(e)}",
            )
