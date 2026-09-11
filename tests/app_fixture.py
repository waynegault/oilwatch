"""A throwaway install root, shared by the service-level suites.

test_service_flows and test_purchases each built their own: two copies of one
idea, with differently-shaped settings dicts, so a new required setting would
have to be added in both and a divergence would surface as an odd failure rather
than an obvious one. Subclass :class:`AppTestCase` instead, and either set
``overrides`` to the suppliers the config should import or clear it and insert
them through the database.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from oilwatch.service import OilWatchApp

#: The settings every service-level test runs against. Keys a test does not care
#: about are left out and the loader's own defaults apply.
SETTINGS: dict[str, Any] = {
    "database_path": "data/oilwatch.sqlite",
    "chart_path": "data/chart.png",
    "time_series_chart_path": "data/ts.ts.png",
    "home": {"label": "Hatton of Fintry, Aberdeenshire", "latitude": 57.2, "longitude": -2.2},
    "quote_quantity_liters": 1000,
    "currency": "GBP",
    "max_quote_age_days": 30,
}

#: Written to config/supplier_overrides.json and imported by ``init()``. Two of
#: the names share a word, so looking a supplier up by "Scottish Fuels" is
#: ambiguous on purpose.
OVERRIDES: list[dict[str, Any]] = [
    {"name": "ValueOils", "website": "https://www.valueoils.com", "connector_type": "manual"},
    {"name": "Scottish Fuels", "website": "https://scottishfuels.co.uk", "connector_type": "manual"},
    {
        "name": "Scottish Fuels Depot",
        "website": "https://scottishfuels.co.uk/depot",
        "connector_type": "manual",
    },
]


class AppTestCase(unittest.TestCase):
    """An ``OilWatchApp`` on a temporary root, with config written for it."""

    settings: dict[str, Any] = SETTINGS
    #: ``None`` writes no overrides file at all, for tests that insert suppliers
    #: themselves and want no help.
    overrides: list[dict[str, Any]] | None = OVERRIDES

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir(parents=True, exist_ok=True)
        (self.root / "data").mkdir(parents=True, exist_ok=True)
        self._write_settings()
        if self.overrides is not None:
            self._write_overrides()
        self.app = OilWatchApp(self.root)

    def _write_settings(self) -> None:
        (self.root / "config" / "settings.json").write_text(
            json.dumps(self.settings), encoding="utf-8"
        )

    def _write_overrides(self) -> None:
        (self.root / "config" / "supplier_overrides.json").write_text(
            json.dumps(self.overrides), encoding="utf-8"
        )

    def _init(self) -> dict[str, int]:
        """Initialise the database and return ``{supplier name: id}``."""
        self.app.init()
        return {s["name"]: s["id"] for s in self.app.suppliers()}
