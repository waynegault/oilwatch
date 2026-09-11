"""API Discovery Tool for finding supplier quote APIs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from playwright.async_api import Page, Playwright, async_playwright

from oilwatch.logging_setup import get_logger

log = get_logger("api_discovery")

# Pause between intercepted requests so discovery cannot burst a supplier.
# Small on purpose: it only needs to break up a flood, not slow the tool down.
REQUEST_DELAY_SECONDS = 0.25


class APIDiscoveryTool:
    """
    Discovers API endpoints used by supplier websites.
    
    Usage:
        tool = APIDiscoveryTool()
        await tool.discover("https://scottishfuels.co.uk")
        print(tool.get_api_endpoints())
    """
    
    def __init__(self, output_dir: Path | None = None) -> None:
        self.output_dir = output_dir or Path.cwd() / "data" / "api_discovery"
        self._playwright: Playwright | None = None
        self._page: Page | None = None
        self._requests: list[dict[str, Any]] = []
        self._responses: list[dict[str, Any]] = []
        self._api_endpoints: dict[str, dict[str, Any]] = {}
    
    async def _setup(self, headless: bool = True) -> Page:
        """Set up browser for API discovery."""
        self._playwright = await async_playwright().start()
        
        browser = await self._playwright.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        
        # Intercept all requests
        await context.route("**/*", self._intercept)
        
        self._page = await context.new_page()
        return self._page
    
    async def _intercept(self, route):
        """Intercept and log all API requests."""
        request = route.request
        url = request.url
        
        # Log all requests
        request_data = {
            "method": request.method,
            "url": url,
            "headers": dict(request.headers),
            "post_data": request.post_data,
            "resource_type": request.resource_type,
        }
        self._requests.append(request_data)
        
        # Pace the requests we let through. Discovery only loads one page, but
        # that page can fire a burst of XHRs, and there was no throttle at all —
        # this keeps it from looking like a flood to the supplier.
        await asyncio.sleep(REQUEST_DELAY_SECONDS)

        # Continue and capture response
        try:
            response = await route.fetch()
            
            # Log API/JSON responses
            if self._is_api_request(url):
                try:
                    body = await response.text()
                    self._responses.append({
                        "url": url,
                        "status": response.status,
                        "headers": dict(response.headers),
                        "body": body[:10000],  # Limit size
                        "content_type": response.headers.get("content-type", ""),
                    })
                    
                    # Try to parse as JSON
                    if "json" in response.headers.get("content-type", "").lower():
                        try:
                            json_data = json.loads(body)
                            self._api_endpoints[url] = {
                                "method": request.method,
                                "request_headers": request_data["headers"],
                                "request_body": request_data["post_data"],
                                "response_status": response.status,
                                "response_data": json_data,
                            }
                        except json.JSONDecodeError as exc:
                            log.debug("intercepted response was not JSON: %s", exc)

                except Exception as exc:  # noqa: BLE001
                    log.debug("could not record intercepted response: %s", exc)
            
            await route.fulfill(response=response)
        except Exception as exc:  # noqa: BLE001 - aborting the route must not raise here
            log.debug("aborting unfulfillable route: %s", exc)
            await route.abort()
    
    def _is_api_request(self, url: str) -> bool:
        """Check if URL looks like an API endpoint."""
        api_indicators = [
            "/api/",
            "/json",
            ".json",
            "/ajax/",
            "/graphql",
            "/rest/",
            "/wp-json/",
            "/v1/",
            "/v2/",
            "quote",
            "price",
            "pricing",
            "cart",
            "checkout",
        ]
        return any(indicator in url.lower() for indicator in api_indicators)
    
    async def discover(
        self,
        url: str,
        actions: list[callable] | None = None,
        timeout: int = 10000,
    ) -> dict[str, Any]:
        """
        Discover APIs on a website.
        
        Args:
            url: Base URL to explore
            actions: Optional list of async functions to execute (e.g., fill forms, click buttons)
            timeout: Time to wait after actions (ms)
        
        Returns:
            Dict with discovered API endpoints
        """
        await self._setup(headless=True)
        
        try:
            # Navigate to page
            if self._page:
                await self._page.goto(url, wait_until="domcontentloaded")
                await self._page.wait_for_timeout(3000)
                
                # Execute any provided actions (form filling, clicking, etc.)
                if actions:
                    for action in actions:
                        try:
                            await action(self._page)
                            await self._page.wait_for_timeout(1000)
                        except Exception as e:
                            print(f"Action failed: {e}")
                
                # Wait for any pending requests
                await self._page.wait_for_timeout(timeout)
                
                return self.get_summary()
                
        finally:
            await self._close()
    
    async def _close(self) -> None:
        """Close browser and clean up."""
        if self._page:
            context = self._page.context
            await context.close()
        if self._playwright:
            await self._playwright.stop()
        self._page = None
        self._playwright = None
    
    def get_api_endpoints(self) -> dict[str, dict[str, Any]]:
        """Get discovered API endpoints with request/response data."""
        return self._api_endpoints
    
    def get_summary(self) -> dict[str, Any]:
        """Get a summary of discovered APIs."""
        # Group endpoints by base path
        grouped: dict[str, list[str]] = {}
        for url in self._api_endpoints.keys():
            # Extract base path
            parts = url.split("?")[0].rstrip("/").split("/")
            base = "/".join(parts[:5]) if len(parts) > 4 else url
            if base not in grouped:
                grouped[base] = []
            grouped[base].append(url)
        
        return {
            "total_requests": len(self._requests),
            "total_api_requests": len([r for r in self._requests if self._is_api_request(r["url"])]),
            "total_responses": len(self._responses),
            "api_endpoints_found": len(self._api_endpoints),
            "endpoint_groups": grouped,
            "endpoints": self._api_endpoints,
        }
    
    def save_results(self, filename: str) -> Path:
        """Save discovery results to a JSON file."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / filename
        
        results = {
            "summary": self.get_summary(),
            "all_requests": self._requests,
            "all_responses": self._responses,
            "api_endpoints": self._api_endpoints,
        }
        
        output_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        return output_path


async def discover_supplier_api(url: str, output_file: str = "") -> dict[str, Any]:
    """
    Convenience function to discover APIs on a supplier website.
    
    Args:
        url: Supplier website URL
        output_file: Optional output filename (saved to data/api_discovery/)
    
    Returns:
        Dict with discovered API endpoints
    """
    tool = APIDiscoveryTool()
    results = await tool.discover(url)
    
    if output_file:
        path = tool.save_results(output_file)
        print(f"Results saved to: {path}")
    
    return results
