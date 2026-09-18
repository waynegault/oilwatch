from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def utcnow_naive() -> datetime:
    """Return the current UTC time as a naive datetime.

    ``datetime.utcnow()`` is deprecated on Python 3.12+. We keep the value
    *naive* (no ``+00:00`` suffix) so that stored ISO-8601 timestamps stay
    string-sortable and consistent with historical rows already in SQLite.
    """
    return datetime.now(UTC).replace(tzinfo=None)


#: The ``status`` vocabulary of a *quote* row, and the gate a consumer branches
#: on. Deliberately three values and no more: ``ok`` carries a price,
#: ``manual_action_required`` means the supplier is contactable instead (its
#: ``reason`` says how), and ``error`` means the attempt itself raised. A
#: consumer that meets a fourth value has no sensible behaviour for it, so this
#: is a closed set rather than a free-form string.
#:
#: Not to be confused with two other status fields in this codebase: a supplier
#: row's ``status`` (``active``/``inactive``/``manual_review``) and the result of
#: a form submission or registration, which have their own vocabularies
#: (``submitted``, ``phone_only``, ``pending``, …). Quote rows are the only ones
#: these three describe.
QUOTE_STATUSES = ("ok", "manual_action_required", "error")


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
    #: Why this quote is not ``ok``, machine-readably; the prose stays in
    #: ``notes``. Deliberately a field rather than more ``status`` values: a
    #: consumer branches on ``status``, so a reason it does not recognise
    #: degrades gracefully where an unrecognised status does not. Connectors
    #: should draw from this set rather than inventing variants:
    #:
    #: - ``no_quote_page`` — no web quote exists (phone/email only)
    #: - ``quote_by_request`` — a quote page exists but only answers a person:
    #:   it takes your details and replies, so there is no price to read. Not
    #:   the same thing as ``no_quote_page``, and the two were once conflated:
    #:   five suppliers with a working quote form were reported as having none
    #: - ``login_not_confirmed`` — an authenticated portal did not sign in
    #: - ``captcha`` — a bot check stopped the flow
    #: - ``site_error`` — the attempt raised: timeout, HTTP error, or a parse
    #:   failure
    #:
    #: ``None`` means unclassified rather than "no reason" — most connectors do
    #: not attribute one yet, and that is not a claim that none applies.
    reason: str | None = None
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
