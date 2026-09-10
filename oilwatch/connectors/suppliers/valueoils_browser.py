"""ValueOils browser connector with auto-login and API discovery."""

from __future__ import annotations

import re
from typing import Any

from playwright.async_api import Page

from oilwatch.connectors.browser_base import BrowserConnector
from oilwatch.identity import load_contact
from oilwatch.logging_setup import get_logger
from oilwatch.models import QuoteResult

log = get_logger("connectors.valueoils")


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
        """
        Log in to ValueOils (optional - quotes work without login).
        
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
            await page.wait_for_timeout(3000)
            
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
            postcode_field = await page.query_selector(
                'input[name="postcode"], input[id*="postcode"], '
                'input[placeholder*="postcode"]'
            )
            
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
                
                # Wait for price to load
                await page.wait_for_timeout(5000)
            
            # Try to extract price from page
            price_per_liter = await self._extract_price(page, quantity_liters)
            
            if price_per_liter:
                total_price = round(price_per_liter * quantity_liters, 2)
                vat_rate = 0.05  # 5% VAT for domestic
                total_with_vat = round(total_price * (1 + vat_rate), 2)
                
                return QuoteResult(
                    supplier_id=int(supplier["id"]),
                    supplier_name=supplier["name"],
                    observed_at=self.now(),
                    quantity_liters=quantity_liters,
                    status="ok",
                    price_per_liter=price_per_liter,
                    total_price=total_with_vat,
                    source="valueoils_browser",
                    notes=f"Price extracted via browser automation for {quantity_liters}L. Ex VAT: £{total_price:.2f}, Inc VAT: £{total_with_vat:.2f}",
                    raw_payload={
                        "quote_url": self.quote_url,
                        "postcode": postcode,
                        "method": "browser_automation",
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
                    price_str = match.group(1)
                    price = float(price_str)
                    
                    # If price is in pence (> 100), convert to pounds
                    if price > 100:
                        return round(price / 100, 4)
                    return round(price, 4)
            
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
                r'Heating\s*Oil.*?Kerosene.*?(\d{2,3}\.\d{2})\s*p.*?900',
                r'900\s*Litres.*?(\d{2,3}\.\d{2})\s*p',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
                if match:
                    price_pence = float(match.group(1))
                    price_per_liter = round(price_pence / 100, 4)
                    total_price = round(price_per_liter * quantity_liters, 2)
                    total_with_vat = round(total_price * 1.05, 2)
                    
                    return QuoteResult(
                        supplier_id=int(supplier["id"]),
                        supplier_name=supplier["name"],
                        observed_at=self.now(),
                        quantity_liters=quantity_liters,
                        status="ok",
                        price_per_liter=price_per_liter,
                        total_price=total_with_vat,
                        source="valueoils_http_fallback",
                        notes=f"Price extracted via HTTP fallback (browser: {error or 'N/A'}). Ex VAT: £{total_price:.2f}, Inc VAT: £{total_with_vat:.2f}",
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
            
        except Exception as e:
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="error",
                source="valueoils_browser",
                notes=f"HTTP fallback failed: {str(e)}",
            )
