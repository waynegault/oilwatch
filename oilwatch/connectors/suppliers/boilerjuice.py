"""BoilerJuice browser connector with auto-login and quote extraction."""

from __future__ import annotations

import re
from typing import Any

from playwright.async_api import Page

from oilwatch.connectors.browser_base import BrowserConnector
from oilwatch.identity import load_contact
from oilwatch.logging_setup import get_logger
from oilwatch.models import OrderResult, QuoteResult
from oilwatch.pricing import normalise_price_per_litre

log = get_logger("connectors.boilerjuice")


class BoilerJuiceBrowserConnector(BrowserConnector):
    """
    BoilerJuice browser connector.
    
    BoilerJuice has an online quote system that requires login.
    Quote URL: https://www.boilerjuice.com/uk/journeys/core/quote
    
    This connector:
    1. Logs in with stored credentials
    2. Navigates to quote page
    3. Fills in postcode and quantity
    4. Extracts price from quote response
    """
    
    def __init__(self) -> None:
        super().__init__()
        self.supplier_key = "boilerjuice"
        self.supplier_name = "BoilerJuice"
        self.base_url = "https://www.boilerjuice.com"
        self.login_url = "https://www.boilerjuice.com/uk/login"
        self.quote_url = "https://www.boilerjuice.com/uk/journeys/core/quote"
    
    async def login(self, page: Page, email: str, password: str) -> bool:
        """
        Log in to BoilerJuice.
        
        Returns True if login successful, False otherwise.
        """
        try:
            await page.goto(self.login_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            
            # Check if already logged in
            if await page.query_selector("a.logout, .account-link, .my-account"):
                return True
            
            # Find and fill login form
            email_field = await page.query_selector('input[name="email"], input[type="email"], input[id*="email"]')
            password_field = await page.query_selector('input[name="password"], input[type="password"], input[id*="password"]')
            login_button = await page.query_selector('button:has-text("Login"), input[value*="Login"], button[type="submit"]')
            
            if email_field and password_field and login_button:
                await email_field.fill(email)
                await password_field.fill(password)
                await login_button.click()
                
                # Wait for navigation
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception as exc:  # noqa: BLE001
                    log.debug("login page did not reach networkidle: %s", exc)
                
                await page.wait_for_timeout(3000)
                
                # Check if login was successful
                if await page.query_selector("a.logout, .account-link, .my-account"):
                    return True
                
                # Check for error
                error = await page.query_selector(".error, .alert-danger, .validation-error")
                if error:
                    error_text = await error.text_content()
                    if error_text and ("invalid" in error_text.lower() or "incorrect" in error_text.lower()):
                        return False
            
            return False
            
        except Exception as e:
            print(f"Login error: {e}")
            return False
    
    async def get_quote_with_browser(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
        page: Page,
    ) -> QuoteResult:
        """
        Get a quote from BoilerJuice using browser automation.
        
        Strategy:
        1. Navigate to quote page
        2. Fill in postcode and quantity
        3. Submit and extract price
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
            
            # Quantity field (usually a dropdown or number input)
            quantity_field = await page.query_selector(
                'input[name="quantity"], input[id*="quantity"], '
                'input[name="litres"], input[id*="litres"], '
                'input[type="number"], select[name="quantity"]'
            )
            
            # Get quote button
            quote_button = await page.query_selector(
                'button:has-text("Quote"), button:has-text("Get Quote"), '
                'button:has-text("Price"), input[value*="Quote"], '
                'button:has-text("Order"), button:has-text("Continue")'
            )
            
            # Fill in fields
            if postcode_field:
                await postcode_field.fill(postcode)
            
            if quantity_field:
                tag = await quantity_field.evaluate("el => el.tagName")
                if tag.upper() == "SELECT":
                    # Dropdown - select closest option
                    options = await quantity_field.query_selector_all("option")
                    for option in options:
                        value = await option.get_attribute("value")
                        if value and str(quantity_liters) in value:
                            await quantity_field.select_option(value)
                            break
                else:
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
                vat_rate = 0.05  # 5% VAT for domestic heating oil
                total_with_vat = round(total_price * (1 + vat_rate), 2)
                
                return QuoteResult(
                    supplier_id=int(supplier["id"]),
                    supplier_name=supplier["name"],
                    observed_at=self.now(),
                    quantity_liters=quantity_liters,
                    status="ok",
                    price_per_liter=price_per_liter,
                    total_price=total_with_vat,
                    source="boilerjuice_browser",
                    notes=f"Price extracted via browser automation for {quantity_liters}L. Ex VAT: £{total_price:.2f}, Inc VAT: £{total_with_vat:.2f}",
                    raw_payload={
                        "quote_url": self.quote_url,
                        "postcode": postcode,
                        "method": "browser_automation",
                    },
                )
            
            # No price found - return manual action required
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="manual_action_required",
                source="boilerjuice_browser",
                notes="Logged in but could not extract automated price. Please complete quote manually at: https://www.boilerjuice.com/uk/journeys/core/quote",
            )
            
        except Exception as e:
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="error",
                source="boilerjuice_browser",
                notes=f"Browser automation error: {str(e)}",
            )
    
    async def _extract_price(self, page: Page, quantity_liters: int) -> float | None:
        """Extract price per litre from the page."""
        try:
            # Get page content
            content = await page.content()
            
            # Look for price patterns
            patterns = [
                r'£?(\d+\.\d{2})\s*(?:per\s*)?litre',
                r'£?(\d+\.\d{2})\s*p(?:ence)?/l',
                r'price.*?£?(\d+\.\d{2})',
                r'total.*?£?(\d+\.\d{2})',
                r'(\d+)p\s*/\s*l',
                r'unit\s*price.*?£?(\d+\.\d{2})',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    price = normalise_price_per_litre(match.group(1))
                    if price is not None:
                        return price

            # Try to find price in specific elements
            price_elements = await page.query_selector_all(
                '.price, .total, .quote-price, [class*="price"], [id*="price"], '
                '.order-summary [class*="amount"]'
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
    
    def place_order(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        agreed_price_per_liter: float,
        context: dict[str, Any],
    ) -> OrderResult:
        """BoilerJuice requires manual order placement via website."""
        return OrderResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            created_at=self.now(),
            quantity_liters=quantity_liters,
            agreed_price_per_liter=agreed_price_per_liter,
            status="manual_action_required",
            notes=f"Order via website: {self.quote_url}. Logged in as {load_contact().email}",
        )
