from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow_naive() -> datetime:
    """Return the current UTC time as a naive datetime.

    ``datetime.utcnow()`` is deprecated on Python 3.12+. We keep the value
    *naive* (no ``+00:00`` suffix) so that stored ISO-8601 timestamps stay
    string-sortable and consistent with historical rows already in SQLite.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(slots=True)
class SupplierCandidate:
    name: str
    website: str
    query: str
    title: str = ""
    snippet: str = ""
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    distance_miles: float | None = None
    status: str = "manual_review"
    connector_type: str = "manual"
    connector_config: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class QuoteResult:
    supplier_id: int
    supplier_name: str
    observed_at: datetime
    quantity_liters: int
    status: str
    price_per_liter: float | None = None
    total_price: float | None = None
    currency: str = "GBP"
    source: str = "unknown"
    notes: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)
    #: When this quote stops being a valid offer. A connector may set it from
    #: what the supplier states on the page; otherwise it defaults below, so
    #: every quote carries a validity.
    valid_until: datetime | None = None

    def to_record(self) -> dict[str, Any]:
        from datetime import timedelta

        payload = asdict(self)
        payload["observed_at"] = self.observed_at.isoformat()
        # A day is the working assumption for a heating-oil quote: the suppliers
        # here re-price daily, and it matches the freshness window the tool uses.
        valid_until = self.valid_until or self.observed_at + timedelta(hours=24)
        payload["valid_until"] = valid_until.isoformat()
        return payload
