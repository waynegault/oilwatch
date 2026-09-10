"""Automated supplier account registration using Playwright."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, Page, Playwright, async_playwright

from oilwatch.credentials import (
    generate_supplier_password,
    get_credential_manager,
    store_supplier_credentials,
)
from oilwatch.identity import load_contact


class AccountRegistrar:
    """
    Automates account registration on supplier websites.
    
    Usage:
        from oilwatch.identity import load_contact
        contact = load_contact()
        registrar = AccountRegistrar()
        await registrar.register_supplier("scottish_fuels", {
            "name": contact.name,
            "email": contact.email,
            "phone": contact.phone,
            "address": "Hatton of Fintray, Aberdeenshire",
            "postcode": contact.postcode,
        })
    """
    
    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._results: list[dict[str, Any]] = []
    
    async def _setup(self) -> Page:
        """Set up browser."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        self._page = await self._context.new_page()
        return self._page
    
    async def _close(self) -> None:
        """Close browser."""
        if self._page:
            await self._page.context.close()
        if self._playwright:
            await self._playwright.stop()
        self._page = None
        self._browser = None
        self._playwright = None
    
    async def _accept_cookies(self, page: Page) -> None:
        """Accept cookie consent banners."""
        cookie_selectors = [
            'button:has-text("Accept"), button:has-text("Accept All"), button:has-text("OK")',
            'button[id*="accept"], button[class*="accept"]',
            '.iubenda-cs-accept-btn, #iubenda-cs-accept-btn',
            '[aria-label*="accept"], [title*="accept"]',
        ]
        
        for selector in cookie_selectors:
            try:
                button = await page.query_selector(selector)
                if button:
                    await button.click(timeout=3000)
                    await page.wait_for_timeout(1000)
                    return
            except Exception:
                pass
        
        # Try to close cookie banner
        try:
            close_btn = await page.query_selector('.iubenda-cs-close, button[aria-label*="close"], .cookie-close')
            if close_btn:
                await close_btn.click(timeout=3000)
                await page.wait_for_timeout(1000)
        except Exception:
            pass
    
    async def register_scottish_fuels(
        self,
        name: str,
        email: str,
        phone: str,
        address: str,
        postcode: str,
    ) -> dict[str, Any]:
        """Register account on Scottish Fuels website."""
        supplier_key = "scottish_fuels"
        supplier_name = "Scottish Fuels"
        password = generate_supplier_password(supplier_name)
        
        result = {
            "supplier": supplier_name,
            "supplier_key": supplier_key,
            "email": email,
            "password": password,
            "status": "pending",
            "message": "",
            "timestamp": datetime.now().isoformat(),
        }
        
        try:
            page = await self._setup()
            
            # Navigate to registration page
            await page.goto("https://scottishfuels.co.uk/my-account/", wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            
            # Handle cookie consent
            await self._accept_cookies(page)
            
            # Check if already registered (logged in)
            if await page.query_selector("a.logout"):
                result["status"] = "already_registered"
                result["message"] = "Already logged in"
                store_supplier_credentials(supplier_key, password, supplier_name, email)
                await self._close()
                return result
            
            # Find registration form - check if we need to click register link
            register_link = await page.query_selector('a:has-text("Register"), a:has-text("Sign Up")')
            if register_link:
                try:
                    await register_link.click()
                    await page.wait_for_timeout(2000)
                except Exception:
                    pass
            
            # Fill registration form
            fields_filled = False
            fields = {
                'input[name="username"]': name.split()[0].lower() if name else "",
                'input[name="email"]': email,
                'input[name="password"]': password,
                'input[name="account_email"]': email,
                'input[name="account_password"]': password,
            }
            
            for selector, value in fields.items():
                try:
                    field = await page.query_selector(selector)
                    if field and value:
                        await field.fill(value)
                        fields_filled = True
                except Exception:
                    pass
            
            if not fields_filled:
                # Try alternative selectors
                alt_fields = {
                    'input[id="reg_email"]': email,
                    'input[id="reg_password"]': password,
                    'input[type="email"]': email,
                    'input[type="password"]': password,
                }
                for selector, value in alt_fields.items():
                    try:
                        field = await page.query_selector(selector)
                        if field:
                            await field.fill(value)
                    except Exception:
                        pass
            
            # Try to find and click register button
            register_button = await page.query_selector(
                'button[name="register"], input[name="register"], '
                'button:has-text("Register"), input[value*="Register"], '
                'button:has-text("Sign Up")'
            )
            
            if register_button:
                try:
                    await register_button.click(timeout=5000)
                    await page.wait_for_timeout(5000)
                except Exception as e:
                    # Button clicked but navigation happened
                    pass
                
                # Check for success or error
                error = await page.query_selector(".woocommerce-error, .error, .alert")
                if error:
                    error_text = await error.text_content()
                    if error_text:
                        if "exists" in error_text.lower():
                            result["status"] = "already_registered"
                            result["message"] = f"Account already exists"
                            store_supplier_credentials(supplier_key, password, supplier_name, email)
                        elif "created" in error_text.lower() or "success" in error_text.lower():
                            result["status"] = "registered"
                            result["message"] = "Registration successful. Check email for verification."
                            store_supplier_credentials(supplier_key, password, supplier_name, email)
                        else:
                            result["status"] = "manual_review"
                            result["message"] = f"Form may have submitted. Check email."
                            store_supplier_credentials(supplier_key, password, supplier_name, email)
                else:
                    # Check for success message
                    success = await page.query_selector(".woocommerce-message, .success, .alert-success")
                    if success:
                        result["status"] = "registered"
                        result["message"] = "Registration successful. Check email for verification."
                        store_supplier_credentials(supplier_key, password, supplier_name, email)
                    else:
                        # Assume form was submitted if we filled fields
                        result["status"] = "manual_review"
                        result["message"] = "Form submitted. Check email for verification link."
                        store_supplier_credentials(supplier_key, password, supplier_name, email)
            else:
                # No register button found - may already be on registration page
                result["status"] = "manual_review"
                result["message"] = "Registration form found. Please complete manually with stored credentials."
                store_supplier_credentials(supplier_key, password, supplier_name, email)
            
            await self._close()
            
        except Exception as e:
            result["status"] = "manual_review"
            result["message"] = f"Auto-registration encountered issues. Credentials stored for manual registration: {password}"
            # Still store credentials for manual use
            store_supplier_credentials(supplier_key, password, supplier_name, email)
        
        return result
    
    async def register_valueoils(
        self,
        name: str,
        email: str,
        phone: str,
        address: str,
        postcode: str,
    ) -> dict[str, Any]:
        """Register account on ValueOils website."""
        supplier_key = "valueoils"
        supplier_name = "ValueOils"
        password = generate_supplier_password(supplier_name)
        
        result = {
            "supplier": supplier_name,
            "supplier_key": supplier_key,
            "email": email,
            "password": password,
            "status": "pending",
            "message": "",
            "timestamp": datetime.now().isoformat(),
        }
        
        try:
            page = await self._setup()
            
            # Navigate to registration page
            await page.goto("https://www.valueoils.com/my-account/", wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            
            # Handle cookie consent
            await self._accept_cookies(page)
            await page.wait_for_timeout(2000)
            
            # Check if already logged in
            if await page.query_selector("a.logout"):
                result["status"] = "already_registered"
                result["message"] = "Already logged in"
                store_supplier_credentials(supplier_key, password, supplier_name, email)
                await self._close()
                return result
            
            # Fill registration form
            fields = {
                'input[name="email"]': email,
                'input[name="password"]': password,
                'input[name="confirm_password"]': password,
                'input[id="reg_email"]': email,
                'input[id="reg_password"]': password,
            }
            
            for selector, value in fields.items():
                try:
                    field = await page.query_selector(selector)
                    if field and value:
                        await field.fill(value)
                except Exception:
                    pass
            
            # Find register button
            register_button = await page.query_selector(
                'button:has-text("Register"), input[value*="Register"], '
                'button:has-text("Sign Up"), button[name="register"]'
            )
            
            if register_button:
                try:
                    await register_button.click(timeout=5000)
                    await page.wait_for_timeout(5000)
                except Exception:
                    pass
                
                # Check for success or error
                error = await page.query_selector(".error, .alert-danger, [class*='error'], .woocommerce-error")
                if error:
                    error_text = await error.text_content()
                    if error_text and "exists" in error_text.lower():
                        result["status"] = "already_registered"
                        result["message"] = "Account already exists"
                        store_supplier_credentials(supplier_key, password, supplier_name, email)
                    elif error_text:
                        result["status"] = "manual_review"
                        result["message"] = f"Form issue: {error_text[:100]}. Credentials stored."
                        store_supplier_credentials(supplier_key, password, supplier_name, email)
                else:
                    result["status"] = "manual_review"
                    result["message"] = "Registration submitted. Check email for verification. Credentials stored."
                    store_supplier_credentials(supplier_key, password, supplier_name, email)
            else:
                result["status"] = "manual_review"
                result["message"] = "Registration form found. Complete manually with stored credentials."
                store_supplier_credentials(supplier_key, password, supplier_name, email)
            
            await self._close()
            
        except Exception as e:
            result["status"] = "manual_review"
            result["message"] = f"Auto-registration encountered issues. Credentials stored: {password}"
            store_supplier_credentials(supplier_key, password, supplier_name, email)
        
        return result
    
    async def register_homefuels_direct(
        self,
        name: str,
        email: str,
        phone: str,
        address: str,
        postcode: str,
    ) -> dict[str, Any]:
        """Register account on HomeFuels Direct website."""
        supplier_key = "homefuels_direct"
        supplier_name = "HomeFuels Direct"
        password = generate_supplier_password(supplier_name)
        
        result = {
            "supplier": supplier_name,
            "supplier_key": supplier_key,
            "email": email,
            "password": password,
            "status": "pending",
            "message": "",
            "timestamp": datetime.now().isoformat(),
        }
        
        try:
            page = await self._setup()
            
            # Navigate to registration page
            await page.goto("https://homefuelsdirect.co.uk/my-account/", wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            
            # Handle cookie consent
            await self._accept_cookies(page)
            await page.wait_for_timeout(2000)
            
            # Check if already logged in
            if await page.query_selector("a.logout"):
                result["status"] = "already_registered"
                result["message"] = "Already logged in"
                store_supplier_credentials(supplier_key, password, supplier_name, email)
                await self._close()
                return result
            
            # Fill registration form
            fields = {
                'input[name="email"]': email,
                'input[name="password"]': password,
                'input[name="confirm_password"]': password,
                'input[id="reg_email"]': email,
                'input[id="reg_password"]': password,
            }
            
            for selector, value in fields.items():
                try:
                    field = await page.query_selector(selector)
                    if field and value:
                        await field.fill(value)
                except Exception:
                    pass
            
            # Find register button
            register_button = await page.query_selector(
                'button:has-text("Register"), input[value*="Register"], '
                'button:has-text("Sign Up"), button[name="register"]'
            )
            
            if register_button:
                try:
                    await register_button.click(timeout=5000)
                    await page.wait_for_timeout(5000)
                except Exception:
                    pass
                
                # Check for success or error
                error = await page.query_selector(".error, .alert, [class*='error'], .woocommerce-error")
                if error:
                    error_text = await error.text_content()
                    if error_text and "exists" in error_text.lower():
                        result["status"] = "already_registered"
                        result["message"] = "Account already exists"
                        store_supplier_credentials(supplier_key, password, supplier_name, email)
                    elif error_text:
                        result["status"] = "manual_review"
                        result["message"] = f"Form issue. Credentials stored."
                        store_supplier_credentials(supplier_key, password, supplier_name, email)
                else:
                    result["status"] = "manual_review"
                    result["message"] = "Registration submitted. Check email for verification."
                    store_supplier_credentials(supplier_key, password, supplier_name, email)
            else:
                result["status"] = "manual_review"
                result["message"] = "Registration form found. Complete manually with stored credentials."
                store_supplier_credentials(supplier_key, password, supplier_name, email)
            
            await self._close()
            
        except Exception as e:
            result["status"] = "manual_review"
            result["message"] = f"Auto-registration encountered issues. Credentials stored: {password}"
            store_supplier_credentials(supplier_key, password, supplier_name, email)
        
        return result
    
    async def register_all_suppliers(
        self,
        name: str,
        email: str,
        phone: str,
        address: str,
        postcode: str,
    ) -> list[dict[str, Any]]:
        """Register accounts on all supplier websites."""
        self._results = []
        
        # Scottish Fuels
        print(f"Registering on Scottish Fuels...")
        result = await self.register_scottish_fuels(name, email, phone, address, postcode)
        self._results.append(result)
        print(f"  Status: {result['status']} - {result['message']}")
        
        # ValueOils
        print(f"Registering on ValueOils...")
        result = await self.register_valueoils(name, email, phone, address, postcode)
        self._results.append(result)
        print(f"  Status: {result['status']} - {result['message']}")
        
        # HomeFuels Direct
        print(f"Registering on HomeFuels Direct...")
        result = await self.register_homefuels_direct(name, email, phone, address, postcode)
        self._results.append(result)
        print(f"  Status: {result['status']} - {result['message']}")
        
        return self._results
    
    def save_results(self, output_path: Path | str) -> Path:
        """Save registration results to JSON file."""
        import json
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(self._results, indent=2), encoding="utf-8")
        return output_path


async def register_all(
    name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    address: str = "Hatton of Fintray, Aberdeenshire",
    postcode: str | None = None,
    headless: bool = True,
) -> list[dict[str, Any]]:
    """
    Convenience function to register on all supplier websites.

    Args:
        name: Full name for registration (defaults to the configured contact)
        email: Email address (defaults to the configured contact email)
        phone: Phone number (defaults to the configured contact phone)
        address: Delivery address
        postcode: Postcode (defaults to the configured delivery postcode)
        headless: Run browser in headless mode

    Returns:
        List of registration results
    """
    contact = load_contact()
    registrar = AccountRegistrar(headless=headless)
    return await registrar.register_all_suppliers(
        name or contact.name,
        email or contact.email,
        phone if phone is not None else contact.phone,
        address,
        postcode or contact.postcode,
    )
