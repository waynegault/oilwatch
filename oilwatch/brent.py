"""Fetch Brent crude spot prices from the US EIA and store them for charting.

The EIA publishes a daily Europe Brent Spot Price (FOB, $/barrel) workbook at
``RBRTEd.xls``. This module downloads it, parses the ``Data 1`` sheet, and
inserts any points newer than what is already in the ``brent_crude`` table.
"""

from __future__ import annotations

from typing import Any

import httpx
import xlrd

from oilwatch.db import Database

EIA_DAILY_BRENT_URL = "https://www.eia.gov/dnav/pet/hist_xls/RBRTEd.xls"
SOURCE = "eia"


def fetch_brent_series(url: str = EIA_DAILY_BRENT_URL) -> list[tuple[str, float]]:
    """Return the full daily Brent series as ``(YYYY-MM-DD, usd_per_barrel)``."""
    response = httpx.get(url, follow_redirects=True, timeout=30)
    response.raise_for_status()
    book = xlrd.open_workbook(file_contents=response.content)
    sheet = book.sheet_by_name("Data 1")

    series: list[tuple[str, float]] = []
    for row_idx in range(3, sheet.nrows):
        serial = sheet.cell(row_idx, 0).value
        price = sheet.cell(row_idx, 1).value
        if not isinstance(serial, (int, float)) or not isinstance(price, (int, float)):
            continue
        day = xlrd.xldate_as_datetime(float(serial), book.datemode).date().isoformat()
        series.append((day, float(price)))
    return series


def update_brent(db: Database) -> dict[str, Any]:
    """Fetch the EIA daily Brent series and insert any new points."""
    series = fetch_brent_series()
    inserted = db.record_brent_many([(day, price, SOURCE) for day, price in series])
    return {"brent_points_inserted": inserted, "latest": series[-1] if series else None}
