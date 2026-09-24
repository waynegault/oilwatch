"""Highland Fuels connector.

Highland Fuels' live quote tool is a PHP app at ``iqo-highland.fuels.app``. It
exposes a plain XML API: POST an XML request to ``/lib/getoffers.php`` and it
returns an XML response with an ``<Offer><UnitPrice>`` in pence per litre
(ex-VAT) and a ``<Total>`` in pence (inc-VAT).

Request::

    <Request>
      <Product>043</Product>
      <Quantity>1000</Quantity>
      <PostCode>AB00 0AA</PostCode>
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
from oilwatch.http import build_client, request_with_retry
from oilwatch.identity import load_contact
from oilwatch.logging_setup import get_logger
from oilwatch.models import QuoteResult
from oilwatch.pricing import inclusive_total

log = get_logger("connectors.highland_fuels")


class HighlandFuelsConnector(BaseConnector):
    quote_url = "https://iqo-highland.fuels.app/lib/getoffers.php"
    product_value = "043"  # DOMESTIC OIL

    def __init__(self) -> None:
        # Shared client: timeouts, browser headers and transport-level retries.
        self.client = build_client(headers={"Content-Type": "application/xml"})

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
            response = request_with_retry(
                self.http_client(),
                "POST",
                self.quote_url,
                content=request_xml,
                headers={"Content-Type": "application/xml"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Highland Fuels quote request failed: %s", exc)
            return self._manual(
                supplier,
                quantity_liters,
                f"HTTP error: {exc}",
                "site_error",
                status="error",
            )

        parsed = self.parse_offers_response(response.text)
        if parsed is None:
            return self._manual(
                supplier,
                quantity_liters,
                "No offer/price in the getoffers.php response.",
                # It answered; there was no offer in it.
                "no_price_found",
            )

        price_per_liter, offer_total = parsed
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status="ok",
            price_per_liter=price_per_liter,
            total_price=inclusive_total(price_per_liter, quantity_liters),
            source="highland_fuels",
            notes=(
                f"Standard Delivery from the Highland Fuels quote app for "
                f"{quantity_liters}L: £{offer_total:.2f} inc VAT = "
                f"£{price_per_liter:.4f}/L. Postcode: {postcode}"
            ),
            raw_payload={
                "quote_url": self.quote_url,
                "postcode": postcode,
                "standard_total": offer_total,
            },
        )

    @staticmethod
    def parse_offers_response(xml_text: str) -> tuple[float, float] | None:
        """The standard offer's (price_per_liter_inc_vat, order_total_gbp).

        Each ``<Offer>`` carries ``UnitPrice`` (pence/L, ex-VAT), ``Total``
        (pence for the order, inc-VAT), a ``Quantity`` and an ``OfferName``.
        ``Total`` is what you actually pay, so it is divided by the ordered
        litres — and the offer named "Standard" is used, falling back to the
        first, so an express option cannot stand in for the standard one.
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None
        offers: list[tuple[str, float, float]] = []
        for offer in root.findall(".//Offer"):
            name = (offer.findtext("OfferName") or "").strip().lower()
            try:
                # findtext gives None for a missing element; float("") raises the
                # ValueError this guard already catches.
                total_pence = float(offer.findtext("Total") or "")
                litres = float(offer.findtext("Quantity") or "")
            except (TypeError, ValueError):
                continue
            if litres <= 0:
                continue
            total = total_pence / 100.0
            offers.append((name, total / litres, total))
        if not offers:
            return None
        _, price_per_liter, total = next((o for o in offers if "standard" in o[0]), offers[0])
        return round(price_per_liter, 4), round(total, 2)

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

        ``reason`` is required rather than defaulted: both callers below know
        which case they are in, and a default would be this function guessing on
        their behalf — the one thing an unclassified row already fails to do.

        ``status`` is ``manual_action_required`` for the response that answered
        without an offer, and ``error`` for the request that raised, because
        ``reason="site_error"`` means the attempt fell over. A consumer branches
        on the status, so filing a transport failure as "no price to give" sends
        the reader to the wrong next step: ``service`` lists the first as a
        supplier doing what it does and only the second as a fault to look at.
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
            source="highland_fuels",
            notes=f"{notes} Contact: {contact}" if contact else notes,
        )

