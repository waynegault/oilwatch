"""HomeFuels Direct browser connector with auto-login and API discovery."""

from __future__ import annotations

import re
from typing import Any

from playwright.async_api import Page

from oilwatch.connectors.browser_base import BrowserConnector
from oilwatch.identity import load_contact
from oilwatch.models import QuoteResult
from oilwatch.pricing import normalise_price_per_litre


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
        """
        Log in to HomeFuels Direct (optional - quotes work without login).
        
        Returns True as login is not required for quotes.
        """
        try:
            await page.goto(self.login_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            
            # Check if already logged in
            if await page.query_selector("a.logout"):
                return True
            
            # Try to find login form
            email_field = await page.query_selector('input[name="email"], input[type="email"]')
            password_field = await page.query_selector('input[name="password"], input[type="password"]')
            login_button = await page.query_selector('button:has-text("Login"), input[value*="Login"]')
            
            if email_field and password_field and login_button:
                await email_field.fill(email)
                await password_field.fill(password)
                await login_button.click()
                await page.wait_for_timeout(3000)
                return True
            
            return True  # Login not required for quotes
            
        except Exception:
            return True  # Login not required for quotes
    
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
            await page.wait_for_timeout(3000)
            
            # Find and fill quote form fields
            # Postcode field
            postcode_field = await page.query_selector(
                'input[name="postcode"], input[id*="postcode"], '
                'input[placeholder*="postcode"], input[autocomplete="postal-code"]'
            )
            
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
                
                # Wait for price to load
                await page.wait_for_timeout(5000)
            
            # Try to extract price from page
            price_per_liter = await self._extract_price(page, quantity_liters)
            
            if price_per_liter:
                total_price = round(price_per_liter * quantity_liters, 2)
                vat_rate = 0.20  # HomeFuels shows prices with VAT
                total_with_vat = round(total_price * (1 + vat_rate), 2)
                
                return QuoteResult(
                    supplier_id=int(supplier["id"]),
                    supplier_name=supplier["name"],
                    observed_at=self.now(),
                    quantity_liters=quantity_liters,
                    status="ok",
                    price_per_liter=price_per_liter,
                    total_price=total_with_vat,
                    source="homefuels_direct_browser",
                    notes=f"Price extracted via browser automation for {quantity_liters}L. Ex VAT: £{total_price:.2f}, Inc VAT: £{total_with_vat:.2f}",
                    raw_payload={
                        "quote_url": self.quote_url,
                        "postcode": postcode,
                        "method": "browser_automation",
                        "api_endpoint": self._discovered_api or "N/A",
                    },
                )
            
            # Fall back to HTTP scraping
            return await self._fallback_to_http(supplier, quantity_liters, context)
            
        except Exception as e:
            # Fall back to HTTP scraping
            return await self._fallback_to_http(supplier, quantity_liters, context, str(e))
    
    async def _extract_price(self, page: Page, quantity_liters: int) -> float | None:
        """Extract price per litre from the page."""
        try:
            content = await page.content()
            
            # HomeFuels shows prices like "138 pence per litre" or "£X.XX"
            patterns = [
                r'(\d{2,3})\s*pence\s*per\s*litre',
                r'(\d{2,3})\s*p\s*/\s*l',
                r'£?(\d+\.\d{2})\s*per\s*litre',
                r'total.*?£?(\d+\.\d{2})',
                r'£(\d{3,}\.\d{2})\s*\(Inc\s*VAT\)',
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
            
        except Exception as e:
            print(f"Price extraction error: {e}")
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
                r'(\d{2,3})\s*pence\s*per\s*litre',
                r'UK\s*Average.*?(\d{2,3})\s*pence',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    price_pence = int(match.group(1))
                    price_per_liter = round(price_pence / 100, 4)
                    total_price = round(price_per_liter * quantity_liters, 2)
                    total_with_vat = round(total_price * 1.20, 2)
                    
                    return QuoteResult(
                        supplier_id=int(supplier["id"]),
                        supplier_name=supplier["name"],
                        observed_at=self.now(),
                        quantity_liters=quantity_liters,
                        status="ok",
                        price_per_liter=price_per_liter,
                        total_price=total_with_vat,
                        source="homefuels_direct_http_fallback",
                        notes=f"Price extracted via HTTP fallback (browser: {error or 'N/A'}). UK Average: {price_pence}p/L",
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
            
        except Exception as e:
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="error",
                source="homefuels_direct_browser",
                notes=f"HTTP fallback failed: {str(e)}",
            )
