from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Any

from oilwatch.models import QuoteReason, QuoteResult, QuoteStatus, utcnow_naive

if TYPE_CHECKING:
    import httpx


class BaseConnector(ABC):
    #: Set by the connectors that talk HTTP directly (via ``build_client``).
    #: Released by :meth:`close`; connectors that drive a browser or only
    #: assemble a manual quote leave it ``None``.
    client: httpx.Client | None = None
    source: str

    @abstractmethod
    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        """Return this connector's quote for a supplier. Implemented by subclasses."""

    @staticmethod
    def now() -> datetime:
        return utcnow_naive()

    def _manual(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        notes: str,
        reason: QuoteReason,
        *,
        status: QuoteStatus = "manual_action_required",
    ) -> QuoteResult:
        """A quote this connector cannot give, with the reason it cannot.

        ``reason`` is required rather than defaulted: a caller that does not know
        which case it is in must be made to say so, because an unclassified row is
        a gap in this repository rather than a statement about the supplier — and
        ``QuoteResult`` refuses a non-``ok`` row without one.

        ``status`` is ``manual_action_required`` when the supplier itself decided
        — it answered and had no price, or its portal wants a sign-in — and
        ``error`` when the attempt fell over, which is what ``reason="site_error"``
        says. The two are kept apart because consumers branch on them: ``service``
        splits the last ask per supplier into the ones with no price to give and
        the ones worth looking at, so a transport failure filed as "nothing to
        give" sends the reader to the wrong next step.

        The phone on the record is contact data, not a route this app offers: it
        never rings a supplier, so the note names an address or a page and leaves
        the number off (AGENTS.md, "the app never rings anyone" — the rule
        ``docs/inspection.md`` row 3.10 checks).
        """
        contact = ", ".join(p for p in (supplier.get("email"), supplier.get("website")) if p)
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=self.now(),
            quantity_liters=quantity_liters,
            status=status,
            reason=reason,
            source=self.source,
            notes=f"{notes} Contact: {contact}" if contact else notes,
        )

    def http_client(self) -> httpx.Client:
        """The HTTP client this connector owns.

        ``client`` is optional because a browser or manual connector never builds
        one. A connector that reaches for it anyway is a bug, so this says so at
        the point of use rather than failing later with "'NoneType' object has no
        attribute 'get'".
        """
        if self.client is None:  # pragma: no cover - every HTTP connector sets one
            raise RuntimeError(f"{type(self).__name__} has no HTTP client")
        return self.client

    def close(self) -> None:
        """Release the HTTP connection pool, if this connector owns one.

        Connectors are built per lookup (:func:`get_connector_for_supplier`) and
        used once, so a client left open here is a pool abandoned on every call.
        """
        if self.client is not None:
            self.client.close()

    def __enter__(self) -> BaseConnector:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
