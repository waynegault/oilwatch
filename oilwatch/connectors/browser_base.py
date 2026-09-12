"""Base browser connector for supplier automation using Playwright."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from oilwatch.connectors.base import BaseConnector
from oilwatch.credentials import get_supplier_credentials, store_supplier_credentials
from oilwatch.identity import load_contact
from oilwatch.logging_setup import get_logger
from oilwatch.models import QuoteResult

log = get_logger("connectors.browser")


class BrowserConnector(BaseConnector, ABC):
    """
    Base class for browser-based supplier connectors.
    
    Provides:
    - Automatic browser management
    - Login functionality with credential storage
    - API request interception
    - Screenshot capture for debugging
    """
    
    def __init__(self) -> None:
        self.supplier_key = ""
        self.supplier_name = ""
        self.login_url = ""
        self.base_url = ""
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._api_requests: list[dict[str, Any]] = []
        self._api_responses: list[dict[str, Any]] = []
    
    @abstractmethod
    async def get_quote_with_browser(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
        page: Page,
    ) -> QuoteResult:
        """
        Get a quote using browser automation.
        
        Override this method to implement supplier-specific quote logic.
        """
        raise NotImplementedError
    
    async def login(self, page: Page, email: str, password: str) -> bool:
        """
        Log in to the supplier website.

        Override this method to implement supplier-specific login logic.

        Returns:
            True if login successful, False otherwise
        """
        # Default implementation - override in subclasses
        raise NotImplementedError

    async def _optional_login(self, page: Page, email: str, password: str) -> bool:
        """Best-effort sign-in for a supplier whose quotes work signed-out.

        Returns True regardless — these suppliers do not need a session to quote
        — but a failure is logged rather than swallowed, so a broken login
        surfaces here instead of later as an unexplained missing price.
        """
        try:
            await page.goto(self.login_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            if await page.query_selector("a.logout"):
                return True  # already signed in

            email_field = await page.query_selector('input[name="email"], input[type="email"]')
            password_field = await page.query_selector('input[name="password"], input[type="password"]')
            login_button = await page.query_selector('button:has-text("Login"), input[value*="Login"]')

            if email_field and password_field and login_button:
                await email_field.fill(email)
                await password_field.fill(password)
                await login_button.click()
                await page.wait_for_timeout(3000)

            return True  # no login form: quoting works signed-out
        except Exception as exc:  # noqa: BLE001 - login is optional; never block the quote
            log.warning("%s optional login failed: %s", self.supplier_name, exc)
            return True
    
    async def _setup_browser(self, headless: bool = True) -> Page:
        """Set up browser and return a new page."""
        self._playwright = await async_playwright().start()
        
        self._browser = await self._playwright.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        
        # Claim the installed Chrome rather than a frozen version: a UA saying
        # Chrome/122 next to a real 140+ build is itself a fingerprint, and it
        # ages out of date every time Chrome updates. Imported lazily so this
        # module does not depend on the Selenium side at import time.
        from oilwatch.browser_auth import detect_chrome_major_version

        chrome_major = detect_chrome_major_version() or 122
        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{chrome_major}.0.0.0 Safari/537.36"
            ),
        )
        
        # Enable request interception for API discovery
        await self._context.route("**/*", self._intercept_request)
        
        self._page = await self._context.new_page()
        return self._page
    
    async def _intercept_request(self, route):
        """Intercept requests to discover APIs."""
        request = route.request
        url = request.url
        
        # Log API requests (filter for JSON/API endpoints)
        if "/api/" in url.lower() or "/json" in url.lower() or ".json" in url.lower():
            self._api_requests.append({
                "method": request.method,
                "url": url,
                "headers": dict(request.headers),
                "post_data": request.post_data,
            })
        
        # Forward the request. A request still in flight when the context comes
        # down lands here after teardown, where fetch() raises "Request context
        # disposed"; that is a race, not a page failure, so it is logged at debug
        # instead of escaping as a traceback that hides the real error.
        try:
            response = await route.fetch()
        except Exception as exc:  # noqa: BLE001
            log.debug("could not forward intercepted request %s: %s", url, exc)
            return
        
        # Log API responses
        if "/api/" in url.lower() or "/json" in url.lower() or ".json" in url.lower():
            try:
                body = await response.text()
                self._api_responses.append({
                    "url": url,
                    "status": response.status,
                    "body": body[:5000],  # Limit size
                })
            except Exception as exc:  # noqa: BLE001
                log.debug("could not read intercepted response body: %s", exc)

        await route.fulfill(response=response)
    
    async def _close_browser(self) -> None:
        """Close browser and clean up."""
        if self._context:
            # Drop in-flight route callbacks first: without this, a request still
            # being handled as the context closes raises inside the callback.
            await self._context.unroute_all(behavior="ignoreErrors")
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
    
    def _get_or_create_credentials(
        self,
        supplier: dict[str, Any],
    ) -> dict[str, str]:
        """Get existing credentials or generate new ones."""
        creds = get_supplier_credentials(self.supplier_key)
        if not creds:
            # Generate new password
            from oilwatch.credentials import generate_supplier_password
            password = generate_supplier_password(self.supplier_name)
            store_supplier_credentials(
                supplier_key=self.supplier_key,
                password=password,
                supplier_name=self.supplier_name,
            )
            creds = {"email": load_contact().email, "password": password}
        return creds
    
    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        """
        Get a quote using browser automation.
        
        This is the synchronous entry point that runs async browser code.
        """
        async def _run() -> QuoteResult:
            try:
                page = await self._setup_browser(headless=True)
                
                # Get credentials
                creds = self._get_or_create_credentials(supplier)
                
                # Navigate to login
                await page.goto(self.login_url, wait_until="domcontentloaded")
                
                # Try to log in
                try:
                    login_success = await self.login(page, creds["email"], creds["password"])
                    if not login_success:
                        # Login failed - may need to register
                        return await self._handle_registration_or_error(
                            supplier, quantity_liters, context, page, creds
                        )
                except Exception as e:
                    # Login threw exception - may need registration
                    return await self._handle_registration_or_error(
                        supplier, quantity_liters, context, page, creds, str(e)
                    )
                
                # Get quote using browser
                result = await self.get_quote_with_browser(supplier, quantity_liters, context, page)
                return result
                
            except Exception as e:
                return QuoteResult(
                    supplier_id=int(supplier["id"]),
                    supplier_name=supplier["name"],
                    observed_at=self.now(),
                    quantity_liters=quantity_liters,
                    status="error",
                    source=f"{self.supplier_key}_browser",
                    notes=f"Browser automation error: {str(e)}",
                )
            finally:
                await self._close_browser()
        
        return asyncio.run(_run())
    
    async def _handle_registration_or_error(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
        page: Page,
        creds: dict[str, str],
        error: str = "",
    ) -> QuoteResult:
        """Handle registration flow or return error with instructions."""
        # Check if there's a registration link
        register_link = await page.query_selector('a:has-text("Register"), a:has-text("Sign Up"), a:has-text("Create Account")')
        
        if register_link:
            # Registration available - return instructions
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="manual_action_required",
                source=f"{self.supplier_key}_browser",
                notes=self._build_registration_instructions(creds, error),
            )
        
        # No registration - return error
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="error",
            source=f"{self.supplier_key}_browser",
            notes=f"Login failed: {error}. Manual action required.",
        )
    
    def _build_registration_instructions(self, creds: dict[str, str], error: str = "") -> str:
        """Build instructions for manual registration."""
        return (
            f"{self.supplier_name.upper()} - Account Registration Required\n\n"
            f"LOGIN ATTEMPT FAILED: {error}\n\n"
            f"CREDENTIALS (use for manual registration):\n"
            f"Email: {creds['email']}\n"
            f"Password: {creds['password']}\n\n"
            f"REGISTRATION STEPS:\n"
            f"1. Go to: {self.login_url}\n"
            f"2. Click 'Register' or 'Sign Up'\n"
            f"3. Use email: {creds['email']}\n"
            f"4. Set password: {creds['password']}\n"
            f"5. Complete registration form\n"
            f"6. Verify email if required\n"
            f"7. Log in and get quote for 1000L\n\n"
            f"Note: Credentials have been saved for future automated access."
        )
    
    async def discover_api(self) -> dict[str, Any]:
        """
        Discover API endpoints by browsing the site.
        
        Returns:
            Dict with discovered API endpoints and patterns
        """
        await self._setup_browser(headless=True)
        try:
            if self._page:
                await self._page.goto(self.base_url, wait_until="domcontentloaded")
                await self._page.wait_for_timeout(3000)  # Let JS load
                
                return {
                    "requests": self._api_requests,
                    "responses": self._api_responses,
                    "base_url": self.base_url,
                }
        finally:
            await self._close_browser()
        return {}
