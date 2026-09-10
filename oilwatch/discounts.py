"""Find discount offers and codes in supplier emails.

Suppliers send things like::

    £10 OFF 500-999 litres - Code: UWCNI154305
    £12 OFF 1,000-1,999 litres - Code: KJHA154306
    £15 OFF 2,000+ litres - Code: THB154307

    Act now - your exclusive discount expires in 48 hours.

That is real money off a quote, and on a 1,000L order £12 is about 1.2p/L —
more than a typical week's price movement. So codes are worth capturing rather
than losing when the email is deleted.

Parsing is deliberately conservative. An amount on its own is enough to record
an offer, but a **code** is only captured when the word "code" precedes it,
because a wrong code is worse than no code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from oilwatch.models import utcnow_naive

# "£10 OFF", "£12.50 off", "£15 discount"
_AMOUNT = re.compile(r"£\s*(?P<amount>\d+(?:\.\d{1,2})?)\s*(?:off|discount)", re.IGNORECASE)

# "500-999 litres", "1,000–1,999 litres" (en dash), "2,000+ litres"
_BAND = re.compile(
    r"(?P<low>\d[\d,]*)\s*(?:[-–—]\s*(?P<high>\d[\d,]*)|(?P<plus>\+))\s*(?:litres|liters|l)\b",
    re.IGNORECASE,
)

# The value may be any case: a supplier really did issue "autumn25", and a code
# we fail to capture is a discount lost at checkout. A plain lower-case word is
# still not a code, so the token must carry an upper-case letter or a digit —
# "use code abcdef" stays ignored, "autumn25" does not.
_CODE = re.compile(
    r"(?:[Cc]ode|[Vv]oucher)\s*[:\s]\s*(?P<code>(?=[A-Za-z0-9]*[A-Z0-9])[A-Za-z0-9]{4,})"
)

# "expires in 48 hours", "expire in 3 days", "expires 48 hours"
_EXPIRY = re.compile(r"expires?\s+(?:in\s+)?(?P<n>\d+)\s*(?P<unit>hour|day)s?", re.IGNORECASE)

_TERMS = re.compile(r"cannot be used|cannot be combined|excludes|in conjunction", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class DiscountOffer:
    """A money-off offer: an amount, optionally scoped, optionally coded."""

    amount_gbp: float
    code: str | None = None
    min_litres: int | None = None
    max_litres: int | None = None
    expires_at: datetime | None = None
    terms: str = ""

    def applies_to(self, litres: int) -> bool:
        """True when this offer covers an order of ``litres``."""
        if self.min_litres is not None and litres < self.min_litres:
            return False
        return not (self.max_litres is not None and litres > self.max_litres)

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or utcnow_naive()) > self.expires_at

    def discounted_total(self, total: float) -> float:
        """Apply the discount to a total, never going below zero."""
        return round(max(0.0, total - self.amount_gbp), 2)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> DiscountOffer:
        """Rebuild an offer from a stored ``discounts`` row."""
        expires_raw = record.get("expires_at")
        expires_at = None
        if expires_raw:
            try:
                expires_at = datetime.fromisoformat(str(expires_raw))
            except ValueError:
                expires_at = None
        return cls(
            amount_gbp=float(record["amount_gbp"]),
            code=record.get("code"),
            min_litres=record.get("min_litres"),
            max_litres=record.get("max_litres"),
            expires_at=expires_at,
            terms=record.get("terms") or "",
        )


def _int_or_none(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value.replace(",", ""))
    except ValueError:
        return None


def _band_from(line: str) -> tuple[int | None, int | None]:
    match = _BAND.search(line)
    if not match:
        return None, None
    low = _int_or_none(match.group("low"))
    if match.group("plus"):
        return low, None  # "2,000+ litres" has no upper bound
    return low, _int_or_none(match.group("high"))


def _expiry_from(text: str, received_at: datetime) -> datetime | None:
    match = _EXPIRY.search(text)
    if not match:
        return None
    count = int(match.group("n"))
    delta = timedelta(hours=count) if match.group("unit").lower().startswith("hour") else timedelta(days=count)
    return received_at + delta


def parse_discounts(text: str, *, received_at: datetime | None = None) -> list[DiscountOffer]:
    """Extract discount offers from an email body.

    Returns an empty list rather than guessing when nothing recognisable is
    present. Any stated expiry ("expires in 48 hours") applies to every offer in
    the message and is resolved against ``received_at``.
    """
    if not text:
        return []

    stamp = received_at or utcnow_naive()
    expires_at = _expiry_from(text, stamp)
    terms_line = next((line.strip()[:200] for line in text.splitlines() if _TERMS.search(line)), "")

    # Walk *every* amount, not just the first one on each line. A marketing email
    # collapses into a single long line once the HTML is stripped — the real
    # ValueOils message did exactly that — so first-per-line captured only the
    # £10 code and silently missed the £12 code that applied to a 1,000L order.
    # Each offer's window runs to the next amount, so a band or code cannot be
    # borrowed from the offer that follows it.
    matches = list(_AMOUNT.finditer(text))
    offers: list[DiscountOffer] = []
    for index, amount in enumerate(matches):
        window_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        window = text[amount.start():window_end]
        try:
            amount_gbp = float(amount.group("amount"))
        except ValueError:
            continue
        code = _CODE.search(window)
        low, high = _band_from(window)
        offers.append(
            DiscountOffer(
                amount_gbp=amount_gbp,
                code=code.group("code") if code else None,
                min_litres=low,
                max_litres=high,
                expires_at=expires_at,
                terms=terms_line,
            )
        )
    return offers


def best_discount_for(
    offers: list[DiscountOffer],
    litres: int,
    *,
    now: datetime | None = None,
) -> DiscountOffer | None:
    """The largest unexpired discount that covers ``litres``, if any."""
    applicable = [offer for offer in offers if offer.applies_to(litres) and not offer.is_expired(now=now)]
    if not applicable:
        return None
    return max(applicable, key=lambda offer: offer.amount_gbp)


def effective_price_per_litre(
    price_per_litre: float,
    litres: int,
    discount: DiscountOffer | None,
) -> float:
    """Spread a discount across an order and return the resulting £/L.

    This is the figure a comparison should quote: the headline price is what the
    supplier lists, the effective price is what the order actually costs.
    """
    if discount is None or litres <= 0:
        return round(price_per_litre, 4)
    return round(discount.discounted_total(price_per_litre * litres) / litres, 4)
