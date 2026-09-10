"""Highland Fuels connector.

Highland Fuels' live quote tool is a PHP app at ``iqo-highland.fuels.app``. It
exposes a plain XML API: POST an XML request to ``/lib/getoffers.php`` and it
returns an XML response with an ``<Offer><UnitPrice>`` in pence per litre
(ex-VAT) and a ``<Total>`` in pence (inc-VAT).

Request::

    <Request>
      <Product>043</Product>
      <Quantity>1000</Quantity>
      <PostCode>AB21 0YA</PostCode>
      <VehicleSize>0</VehicleSize>
      <PromoCode></PromoCode>
      <RetQtyPrices>N</RetQtyPrices>
      <RetAddressDet>Y</RetAddressDet>
    </Request>

Response (abridged)::

    <Response>
      <ResultStatus>1</ResultStatus>
      <Offers>
        <Offer>
          <Quantity>1000</Quantity>
          <DeliveryDate>by 18-09-2026</DeliveryDate>
          <UnitPrice>109.2</UnitPrice>
          <Total>114660</Total>
          ...
        </Offer>
      </Offers>
    </Response>
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import httpx

from oilwatch.connectors.base import BaseConnector
from oilwatch.identity import load_contact
from oilwatch.models import OrderResult, QuoteResult
from oilwatch.pricing import DOMESTIC_VAT_RATE, apply_vat, inclusive_total, pence_to_pounds


class HighlandFuelsConnector(BaseConnector):
    quote_url = "https://iqo-highland.fuels.app/lib/getoffers.php"
    product_value = "043"  # DOMESTIC OIL

    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        postcode = context.get("postcode", "") or load_contact().postcode
        config = supplier.get("connector_config") or {}
        product_value = config.get("product_value", self.product_value)

        request_xml = (
            "<Request>"
            f"<Product>{product_value}</Product>"
            f"<Quantity>{quantity_liters}</Quantity>"
            f"<PostCode>{postcode}</PostCode>"
            "<VehicleSize>0</VehicleSize>"
            "<PromoCode></PromoCode>"
            "<RetQtyPrices>N</RetQtyPrices>"
            "<RetAddressDet>Y</RetAddressDet>"
            "</Request>"
        )

        try:
            response = httpx.post(
                self.quote_url,
                content=request_xml,
                headers={"Content-Type": "application/xml"},
                timeout=30.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return self._manual(supplier, quantity_liters, f"HTTP error: {exc}")

        ex_vat_price = self.parse_offers_response(response.text)
        if ex_vat_price is None:
            return self._manual(supplier, quantity_liters, "No offer/price in the getoffers.php response.")

        price_per_liter = apply_vat(ex_vat_price, DOMESTIC_VAT_RATE)
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="ok",
            price_per_liter=price_per_liter,
            total_price=inclusive_total(price_per_liter, quantity_liters),
            source="highland_fuels",
            notes=f"Price from Highland Fuels quote app for {quantity_liters}L (ex-VAT £{ex_vat_price:.4f}/L, inc-VAT £{price_per_liter:.4f}/L). Postcode: {postcode}",
            raw_payload={"quote_url": self.quote_url, "postcode": postcode, "price_ex_vat": ex_vat_price},
        )

    @staticmethod
    def parse_offers_response(xml_text: str) -> float | None:
        """Extract the ex-VAT price-per-litre from the getoffers.php XML.

        ``UnitPrice`` is pence per litre (ex-VAT). Return the cheapest offer.
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None
        unit_prices = []
        for offer in root.findall(".//Offer"):
            unit = offer.findtext("UnitPrice")
            if unit:
                try:
                    unit_prices.append(pence_to_pounds(float(unit)))
                except (TypeError, ValueError):
                    continue
        if not unit_prices:
            return None
        return round(min(unit_prices), 4)

    def _manual(self, supplier: dict[str, Any], quantity_liters: int, notes: str) -> QuoteResult:
        contact = ", ".join(p for p in [supplier.get("phone"), supplier.get("email"), supplier.get("website")] if p)
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="manual_action_required",
            source="highland_fuels",
            notes=f"{notes} Contact: {contact or 'supplier website'}",
        )

    def place_order(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        agreed_price_per_liter: float,
        context: dict[str, Any],
    ) -> OrderResult:
        return OrderResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            created_at=self.now(),
            quantity_liters=quantity_liters,
            agreed_price_per_liter=agreed_price_per_liter,
            status="manual_action_required",
            notes="Order via the Highland Fuels quote tool or by phone (0800 224 224).",
        )
