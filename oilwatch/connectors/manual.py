from __future__ import annotations

from typing import Any

from oilwatch.connectors.base import BaseConnector
from oilwatch.models import QuoteResult


class ManualConnector(BaseConnector):
    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        config = supplier.get("connector_config") or {}
        order_page = config.get("order_page")
        # The phone on the record is contact data, not a route this app offers:
        # it asks by form or by email and never rings a supplier, so the note
        # names the address and the site and leaves the number off. A note
        # carrying a number is a route a reader may still take.
        contact = ", ".join(
            part for part in [supplier.get("email"), supplier.get("website")] if part
        )
        # Whether a quote page exists changes what this gap means, so it decides
        # the reason. "No quote page" says there is nowhere to ask; "quote by
        # request" says there is a page and it answers a person. Reporting the
        # first for a supplier that has the second tells the reader the opposite
        # of the truth about where to go next.
        reason = "quote_by_request" if order_page else "no_quote_page"
        notes = f"Manual quote required for {quantity_liters}L."
        if contact:
            notes += f" Contact via: {contact}."
        if order_page:
            notes += f" Quote page: {order_page}"
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="manual_action_required",
            # Not a failure and not a broken connector: this supplier has no
            # price the app can read, so the route in the note is the answer.
            reason=reason,
            source="manual",
            notes=notes,
            raw_payload={"order_page": order_page},
        )

