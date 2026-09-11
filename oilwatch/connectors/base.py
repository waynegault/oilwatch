from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Any

from oilwatch.models import QuoteResult, utcnow_naive

if TYPE_CHECKING:
    import httpx


class BaseConnector(ABC):
    #: Set by the connectors that talk HTTP directly (via ``build_client``).
    #: Released by :meth:`close`; connectors that drive a browser or only
    #: assemble a manual quote leave it ``None``.
    client: httpx.Client | None = None

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
