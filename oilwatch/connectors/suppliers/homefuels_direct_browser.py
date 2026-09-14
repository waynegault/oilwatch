"""HomeFuels Direct browser connector.

HomeFuels publishes one live figure on its price pages:

    <h3>Our live average price:<br>
        <span id="currentLivePrice" ncwce="true">112.87</span>
        pence / litre</h3>

It is server-rendered and public — the HTTP connector reads the same span with
no sign-in — so the browser connector reads it too rather than driving the
enquiry form. That form is a collapsed multi-step Contact-Form-7 whose postcode
control is present but not interactable, which is what made the earlier
form-filling path time out and fall back to a manual quote.
"""

from __future__ import annotations

import re
from typing import Any

from playwright.async_api import Page

from oilwatch.connectors.browser_base import BrowserConnector
from oilwatch.identity import load_contact
from oilwatch.logging_setup import get_logger
from oilwatch.models import QuoteResult
from oilwatch.pricing import apply_vat, pence_to_pounds

log = get_logger("connectors.homefuels_direct")

#: The live figure, pence per litre ex-VAT.
_LIVE_PRICE_SELECTOR = "#currentLivePrice"

#: The same span's value in the raw HTML, for the HTTP fallback's text scan.
_LIVE_PRICE_RE = r'id=["\']?currentLivePrice["\']?[^>]*>\s*(\d+(?:\.\d{1,2})?)\s*<'

#: The page's "NN pence per litre" wording, as a second fallback.
_PENCE_PER_LITRE = r'(\d{2,3})\s*pence\s*per\s*litre'


class HomeFuelsDirectBrowserConnector(BrowserConnector):
    """
    HomeFuels Direct browser connector.

    Navigates to the Aberdeenshire price page and reads the live average price
    per litre. The figure is ex-VAT, so it goes through the shared
    inclusive-of-5% conversion like every other connector.
    """

    def __init__(self) -> None:
        super().__init__()
        self.supplier_key = "homefuels_direct"
        self.supplier_name = "HomeFuels Direct"
        self.base_url = "https://homefuelsdirect.co.uk"
        self.login_url = "https://homefuelsdirect.co.uk/my-account/"
        self.quote_url = "https://homefuelsdirect.co.uk/home/heating-oil-prices/aberdeenshire"
        self._requires_login = False

    async def login(self, page: Page, email: str, password: str) -> bool:
        """Optional sign-in; HomeFuels quotes work signed-out (see the base)."""
        return await self._optional_login(page, email, password)

    async def get_quote_with_browser(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
        page: Page,
    ) -> QuoteResult:
        """Read HomeFuels' live average price per litre from the price page."""
        postcode = context.get("postcode", "") or load_contact().postcode
        try:
            await page.goto(self.quote_url, wait_until="domcontentloaded")

            ex_vat_price = await self._read_live_price(page)
            if ex_vat_price is not None:
                return self._quote_from_ex_vat(
                    supplier,
                    quantity_liters,
                    ex_vat_price,
                    source="homefuels_direct_browser",
                    notes=(
                        f"Live average price read from {self.quote_url} for "
                        f"{quantity_liters}L (Ex VAT: £{ex_vat_price:.4f}/L, "
                        f"Inc VAT: £{apply_vat(ex_vat_price):.4f}/L)."
                    ),
                    raw_payload={
                        "quote_url": self.quote_url,
                        "postcode": postcode,
                        "method": "browser_automation",
                    },
                )

            # No figure on the page: fall back to HTTP scraping.
            return await self._fallback_to_http(supplier, quantity_liters, context)

        except Exception as e:  # noqa: BLE001 - any browser failure degrades to the HTTP fallback
            # Fall back to HTTP scraping
            return await self._fallback_to_http(supplier, quantity_liters, context, str(e))

    async def _read_live_price(self, page: Page) -> float | None:
        """Read the live average price (pence per litre, ex-VAT) from the span.

        Bounded-waits for the figure to render, then returns it as GBP per
        litre. ``None`` when the span is absent or carries no figure, so the
        caller falls back to HTTP rather than inventing a price.
        """

        async def ready() -> bool:
            element = await page.query_selector(_LIVE_PRICE_SELECTOR)
            if element is None:
                return False
            return bool(re.search(r"\d", (await element.text_content() or "")))

        await self._wait_for(page, ready, what="the HomeFuels live price")

        element = await page.query_selector(_LIVE_PRICE_SELECTOR)
        if element is None:
            return None
        match = re.search(r"(\d{2,3}(?:\.\d{1,2})?)", await element.text_content() or "")
        if not match:
            return None
        return pence_to_pounds(match.group(1))

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

            # Extract price using regex. The span is the same figure the browser
            # path reads; the pence wordings cover a fall back to the national
            # page.
            patterns = [
                _LIVE_PRICE_RE,
                _PENCE_PER_LITRE,
                r'UK\s*Average.*?(\d{2,3})\s*pence',
            ]

            for pattern in patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    # Every figure here is pence per litre, ex-VAT.
                    price_pence = float(match.group(1))
                    return self._quote_from_ex_vat(
                        supplier,
                        quantity_liters,
                        pence_to_pounds(price_pence),
                        source="homefuels_direct_http_fallback",
                        notes=(
                            f"Price extracted via HTTP fallback (browser: {error or 'N/A'}). "
                            f"Live price: {price_pence:.2f}p/L ex VAT"
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
