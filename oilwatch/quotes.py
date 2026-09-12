from __future__ import annotations

from typing import Any

from oilwatch.connectors import get_connector_for_supplier
from oilwatch.identity import load_contact
from oilwatch.models import QuoteResult


class QuoteService:
    def __init__(self, currency: str, home_label: str) -> None:
        self.currency = currency
        self.home_label = home_label

    def quote_supplier(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        postcode: str | None = None,
        prefer_browser: bool = False,
    ) -> QuoteResult:
        if not postcode:
            # These flows fill a postcode-only quote form, and a None reaches
            # them as an empty string: the supplier then answers "a postcode is
            # required" on what is still the form page, which reads as a broken
            # connector rather than a missing argument. Fall back to the
            # configured delivery postcode, as the connectors do for email.
            postcode = load_contact().postcode
        with get_connector_for_supplier(supplier, prefer_browser=prefer_browser) as connector:
            result = connector.quote(
                supplier=supplier,
                quantity_liters=quantity_liters,
                context={
                    "postcode": postcode,
                    "home_label": self.home_label,
                },
            )
        result.currency = self.currency
        return result

