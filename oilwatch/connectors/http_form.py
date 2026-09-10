from __future__ import annotations

import re
from typing import Any

import httpx

from oilwatch.connectors.base import BaseConnector
from oilwatch.models import OrderResult, QuoteResult
from oilwatch.pricing import apply_vat, inclusive_total, normalise_price_per_litre


class HTTPFormConnector(BaseConnector):
    def __init__(self) -> None:
        self.client = httpx.Client(
            follow_redirects=True,
            headers={"User-Agent": "OilWatch/0.1 (+https://github.com/)"},
            timeout=20.0,
        )

    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        config = supplier.get("connector_config", {})
        response = self._send_request(
            method=config.get("quote_method", "POST"),
            url=config["quote_url"],
            fields=config.get("quote_fields", {}),
            supplier=supplier,
            quantity_liters=quantity_liters,
            agreed_price_per_liter=None,
            context=context,
        )
        price_match = re.search(config["price_regex"], response.text, re.IGNORECASE)
        if not price_match:
            raise ValueError(f"No price matched on quote response for {supplier['name']}")
        price = self._normalise_price(price_match.group(1), config)
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

    def place_order(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        agreed_price_per_liter: float,
        context: dict[str, Any],
    ) -> OrderResult:
        config = supplier.get("connector_config", {})
        if "order_url" not in config:
            return OrderResult(
                supplier_id=int(supplier["id"]),
                supplier_name=supplier["name"],
                created_at=self.now(),
                quantity_liters=quantity_liters,
                agreed_price_per_liter=agreed_price_per_liter,
                status="manual_action_required",
                notes="No automated order_url configured for this supplier.",
            )
        response = self._send_request(
            method=config.get("order_method", "POST"),
            url=config["order_url"],
            fields=config.get("order_fields", {}),
            supplier=supplier,
            quantity_liters=quantity_liters,
            agreed_price_per_liter=agreed_price_per_liter,
            context=context,
        )
        reference = None
        if config.get("reference_regex"):
            match = re.search(config["reference_regex"], response.text, re.IGNORECASE)
            if match:
                reference = match.group(1)
        return OrderResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            created_at=self.now(),
            quantity_liters=quantity_liters,
            agreed_price_per_liter=agreed_price_per_liter,
            status="submitted",
            reference=reference,
            raw_payload={"url": str(response.url), "status_code": response.status_code},
        )

    def _send_request(
        self,
        method: str,
        url: str,
        fields: dict[str, Any],
        supplier: dict[str, Any],
        quantity_liters: int,
        agreed_price_per_liter: float | None,
        context: dict[str, Any],
    ) -> httpx.Response:
        payload = {
            key: self._render_value(value, supplier, quantity_liters, agreed_price_per_liter, context)
            for key, value in fields.items()
        }
        response = self.client.request(method.upper(), url, data=payload)
        response.raise_for_status()
        return response

    @staticmethod
    def _render_value(
        value: Any,
        supplier: dict[str, Any],
        quantity_liters: int,
        agreed_price_per_liter: float | None,
        context: dict[str, Any],
    ) -> Any:
        if not isinstance(value, str):
            return value
        return value.format(
            quantity_liters=quantity_liters,
            agreed_price_per_liter=agreed_price_per_liter,
            postcode=context.get("postcode", ""),
            home_label=context.get("home_label", ""),
            supplier_name=supplier.get("name", ""),
        )

    @staticmethod
    def _normalise_price(text: str, config: dict[str, Any]) -> float:
        price = normalise_price_per_litre(text)
        if price is None:
            raise ValueError(f"Could not parse price from {text!r}")
        # The scraped value is assumed to already include VAT unless the
        # supplier config declares an ex-VAT rate to apply.
        vat_rate = config.get("vat_rate")
        if vat_rate:
            price = apply_vat(price, float(vat_rate))
        return price
