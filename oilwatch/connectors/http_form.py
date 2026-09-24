from __future__ import annotations

import re
from typing import Any

import httpx

from oilwatch.connectors.base import BaseConnector
from oilwatch.http import build_client
from oilwatch.models import QuoteResult
from oilwatch.pricing import apply_vat, inclusive_total, normalise_price_per_litre


class HTTPFormConnector(BaseConnector):
    def __init__(self) -> None:
        # Shared client (see oilwatch/http.py): timeouts plus transport-level
        # retries. Keeps the honest OilWatch user agent rather than a browser's.
        self.client = build_client(
            timeout=20.0,
            headers={"User-Agent": "OilWatch/0.1 (+https://github.com/)"},
        )

    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        config = supplier.get("connector_config", {})
        quote_url = config.get("quote_url", "")
        pattern = config.get("price_regex")
        if not quote_url or not pattern:
            # The one failure that still raises: a register entry that names no
            # URL or no price pattern is a configuration error, and every reason
            # the contract defines describes what a *site* did. A row would
            # misdescribe it; `quote-all` reports the raise as an error row
            # anyway, and `quote <id>` names the missing setting outright.
            missing = "quote_url" if not quote_url else "price_regex"
            raise ValueError(f"Supplier {supplier['name']} is missing {missing} configuration.")

        try:
            response = self._send_request(
                method=config.get("quote_method", "POST"),
                url=quote_url,
                fields=config.get("quote_fields", {}),
                supplier=supplier,
                quantity_liters=quantity_liters,
                context=context,
            )
        except httpx.HTTPError as exc:
            return self._manual(
                supplier,
                quantity_liters,
                f"HTTP error posting to {quote_url}: {exc}",
                "site_error",
                status="error",
            )

        price_match = re.search(pattern, response.text, re.IGNORECASE)
        if not price_match:
            # It answered and carried nothing this pattern could read, which is
            # what no_price_found means — not a fault in the retrieval.
            return self._manual(
                supplier,
                quantity_liters,
                f"No price matched on the quote response from {response.url}.",
                "no_price_found",
            )

        price = self._normalise_price(price_match.group(1), config)
        if price is None:
            # Matched, and not a number: a parse failure, so `site_error`.
            return self._manual(
                supplier,
                quantity_liters,
                f"Could not read {price_match.group(1)!r} in the quote response as a price.",
                "site_error",
                status="error",
            )
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="ok",
            price_per_liter=price,
            total_price=inclusive_total(price, quantity_liters),
            source="http_form",
            raw_payload={"url": str(response.url), "status_code": response.status_code},
        )

    def _manual(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        notes: str,
        reason: str,
        *,
        status: str = "manual_action_required",
    ) -> QuoteResult:
        """A quote this connector cannot give, with the reason it cannot.

        ``status`` is ``manual_action_required`` when the site itself decided —
        it answered and had no price — and ``error`` when the attempt fell over,
        which is what ``reason="site_error"`` says. A consumer branches on the
        difference, so a post that timed out reported as "nothing to give" sends
        the reader to the wrong next step.
        """
        # The phone on the record is contact data, not a route this app offers:
        # it never rings a supplier, so the note names an address or a page.
        contact = ", ".join(p for p in [supplier.get("email"), supplier.get("website")] if p)
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status=status,
            reason=reason,
            source="http_form",
            notes=f"{notes} Contact: {contact}" if contact else notes,
        )

    def _send_request(
        self,
        method: str,
        url: str,
        fields: dict[str, Any],
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> httpx.Response:
        payload = {
            key: self._render_value(value, supplier, quantity_liters, context)
            for key, value in fields.items()
        }
        response = self.http_client().request(method.upper(), url, data=payload)
        response.raise_for_status()
        return response

    @staticmethod
    def _render_value(
        value: Any,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> Any:
        # The placeholders a quote_fields mapping may use. There was an
        # ``agreed_price_per_liter`` among them, for a field that quoted back a
        # price the app had already agreed; that is the automated ordering path,
        # which is dropped, and it was only ever passed ``None`` — so a config
        # using the placeholder silently sent the string "None".
        if not isinstance(value, str):
            return value
        return value.format(
            quantity_liters=quantity_liters,
            postcode=context.get("postcode", ""),
            home_label=context.get("home_label", ""),
            supplier_name=supplier.get("name", ""),
        )

    @staticmethod
    def _normalise_price(text: str, config: dict[str, Any]) -> float | None:
        """The captured text as GBP per litre, or ``None`` if it is not a number."""
        price = normalise_price_per_litre(text)
        if price is None:
            return None
        # The scraped value is assumed to already include VAT unless the
        # supplier config declares an ex-VAT rate to apply.
        vat_rate = config.get("vat_rate")
        if vat_rate:
            price = apply_vat(price, float(vat_rate))
        return price
