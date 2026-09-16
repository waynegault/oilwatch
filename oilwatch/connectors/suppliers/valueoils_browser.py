"""ValueOils browser connector.

ValueOils' "Quick Quote" is an ASP.NET WebForms panel on the regional page: a
postcode, an email and a quantity, then "Show Price", which redirects to
``/Quote.aspx`` with a table of delivery options. Each option states the fuel
ppl *excluding VAT* beside the **Total You Pay**, which — as the page says —
"includes vat, commission and any chargeable delivery option selected". The
total is therefore the only figure comparable with the other suppliers'
delivered prices, and the earlier connector's ``ppl × 1.05`` dropped ValueOils'
commission.

This connector fills that form and reads the **Standard Delivery** option's
total. The site stalls when the browser's requests are proxied back through
Python, so this connector opts out of interception (see ``_intercept_requests``)
— which is also why the HTTP connector used to win for this domain.
"""

from __future__ import annotations

import re
from typing import Any

from oilwatch.connectors.browser_base import BrowserConnector
from oilwatch.connectors.protocols import PageLike
from oilwatch.identity import load_contact
from oilwatch.logging_setup import get_logger
from oilwatch.models import QuoteResult
from oilwatch.pricing import inclusive_total

log = get_logger("connectors.valueoils")

#: The Quick Quote controls (ASP.NET WebForms ids).
_POSTCODE_SELECTOR = "#sgcPriceChecker_txtQuotePostcode"
_EMAIL_SELECTOR = "#sgcPriceChecker_txtQuoteEmail"
_QUANTITY_SELECTOR = "#sgcPriceChecker_txtQuantity"
_SUBMIT_SELECTOR = "#sgcPriceChecker_btnShowPrices"

#: "Standard Delivery - Estimated ... £1,187.55". The first £ after the label is
#: that option's Total You Pay; the fuel ppl is stated in pence with no £ sign,
#: so this cannot pick up the per-litre figure by mistake.
_STANDARD_TOTAL_RE = r"Standard Delivery[\s\S]{0,700}?£\s*([\d,]+\.\d{2})"


class ValueOilsBrowserConnector(BrowserConnector):
    """Read ValueOils' standard-delivery, inclusive-of-VAT Quick Quote total."""

    _intercept_requests = False

    def __init__(self) -> None:
        super().__init__()
        self.supplier_key = "valueoils"
        self.supplier_name = "ValueOils"
        self.base_url = "https://www.valueoils.com"
        self.login_url = "https://www.valueoils.com/my-account/"
        self.quote_url = "https://www.valueoils.com/regions/scotland/aberdeenshire/"
        self._requires_login = False

    async def login(self, page: PageLike, email: str, password: str) -> bool:
        """Optional sign-in; ValueOils quotes work signed-out (see the base)."""
        return await self._optional_login(page, email, password)

    async def get_quote_with_browser(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
        page: PageLike,
    ) -> QuoteResult:
        """Fill the Quick Quote and read the Standard Delivery total."""
        postcode = context.get("postcode", "") or load_contact().postcode
        try:
            await page.goto(self.quote_url, wait_until="domcontentloaded")
            await self._fill_quick_quote(page, postcode, quantity_liters)

            standard_total = await self._read_standard_total(page)
            if standard_total is not None:
                # The total is already inclusive of VAT and commission, so it is
                # divided by the ordered litres rather than uplifted again.
                price_per_liter = round(standard_total / quantity_liters, 4)
                return QuoteResult(
                    supplier_id=int(supplier["id"]),
                    supplier_name=supplier["name"],
                    observed_at=self.now(),
                    quantity_liters=quantity_liters,
                    status="ok",
                    price_per_liter=price_per_liter,
                    total_price=inclusive_total(price_per_liter, quantity_liters),
                    source="valueoils_browser",
                    notes=(
                        f"Standard Delivery from the ValueOils Quick Quote for "
                        f"{quantity_liters}L: £{standard_total:.2f} inc VAT "
                        f"(incl. commission) = £{price_per_liter:.4f}/L."
                    ),
                    raw_payload={
                        "quote_url": self.quote_url,
                        "postcode": postcode,
                        "method": "quick_quote",
                        "standard_delivery_total": standard_total,
                    },
                )

            # No delivery table: fall back to the regional-page HTTP figures.
            return await self._fallback_to_http(supplier, quantity_liters, context)

        except Exception as e:  # noqa: BLE001 - any browser failure degrades to the HTTP fallback
            return await self._fallback_to_http(supplier, quantity_liters, context, str(e))

    async def _fill_quick_quote(self, page: PageLike, postcode: str, quantity_liters: int) -> None:
        """Fill the Quick Quote and submit it. Raises when the form is absent."""
        postcode_field = await page.query_selector(_POSTCODE_SELECTOR)
        if postcode_field is None:
            raise RuntimeError("the ValueOils Quick Quote postcode field was not found")
        await postcode_field.fill(postcode)

        email_field = await page.query_selector(_EMAIL_SELECTOR)
        if email_field is not None:
            await email_field.fill(load_contact().email)

        quantity_field = await page.query_selector(_QUANTITY_SELECTOR)
        if quantity_field is not None:
            await quantity_field.fill(str(quantity_liters))

        submit = await page.query_selector(_SUBMIT_SELECTOR)
        if submit is None:
            raise RuntimeError("the ValueOils 'Show Price' button was not found")
        await submit.click()

    async def _read_standard_total(self, page: PageLike) -> float | None:
        """The Standard Delivery total once the results have rendered."""
        await self._wait_for(
            page, lambda: self._standard_total(page), timeout_ms=25000, what="the ValueOils Quick Quote"
        )
        return await self._standard_total(page)

    @staticmethod
    async def _standard_total(page: PageLike) -> float | None:
        try:
            content = await page.content()
        except Exception:  # noqa: BLE001 - an unreadable page is simply not ready
            return None
        match = re.search(_STANDARD_TOTAL_RE, content, re.IGNORECASE)
        if not match:
            return None
        return float(match.group(1).replace(",", ""))

    async def _fallback_to_http(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
        error: str = "",
    ) -> QuoteResult:
        """Use the regional page's own "Total You Pay", which includes commission.

        The page prices 500L and 900L tiers; the 900L "Total You Pay" is the
        standard-delivery cost at the tier our orders sit in (900L+).
        """
        import httpx

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                response = await client.get(self.quote_url)
                response.raise_for_status()
                content = response.text

            match = re.search(
                r"Live\s*Heating\s*Oil\s*Prices[\s\S]{0,800}?Total\s*You\s*Pay"
                r"[\s\S]{0,120}?£\s*[\d,]+\.\d{2}"
                r"[\s\S]{0,80}?£\s*([\d,]+\.\d{2})",
                content,
                re.IGNORECASE,
            )
            if match:
                tier_total = float(match.group(1).replace(",", ""))
                price_per_liter = round(tier_total / 900, 4)
                return QuoteResult(
                    supplier_id=int(supplier["id"]),
                    supplier_name=supplier["name"],
                    observed_at=self.now(),
                    quantity_liters=quantity_liters,
                    status="ok",
                    price_per_liter=price_per_liter,
                    total_price=inclusive_total(price_per_liter, quantity_liters),
                    source="valueoils_http_fallback",
                    notes=(
                        f"900L Standard total from the ValueOils regional page "
                        f"(browser: {error or 'N/A'}): £{tier_total:.2f} inc VAT "
                        f"(incl. commission) = £{price_per_liter:.4f}/L."
                    ),
                    raw_payload={
                        "url": self.quote_url,
                        "tier_litres": 900,
                        "tier_total": tier_total,
                    },
                )

            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="manual_action_required",
                source="valueoils_browser",
                notes=f"Could not extract the standard-delivery total. Browser error: {error}",
            )

        except Exception as e:  # noqa: BLE001 - reported as an error quote rather than raised
            return QuoteResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                observed_at=self.now(),
                quantity_liters=quantity_liters,
                status="error",
                source="valueoils_browser",
                notes=f"HTTP fallback failed: {e!s}",
            )
