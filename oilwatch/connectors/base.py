from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from oilwatch.models import OrderResult, QuoteResult, utcnow_naive


class BaseConnector(ABC):
    @abstractmethod
    def quote(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        context: dict[str, Any],
    ) -> QuoteResult:
        raise NotImplementedError

    @abstractmethod
    def place_order(
        self,
        supplier: dict[str, Any],
        quantity_liters: int,
        agreed_price_per_liter: float,
        context: dict[str, Any],
    ) -> OrderResult:
        raise NotImplementedError

    @staticmethod
    def now() -> datetime:
        return utcnow_naive()

