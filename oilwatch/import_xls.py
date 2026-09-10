"""Import historical prices from the user's manual tracking spreadsheet.

This is a **historical migration aid, not a source of truth** — the database is
authoritative for suppliers and prices. The workbook simply predates OilWatch,
and its path is now passed explicitly rather than defaulted, because it lives on
a drive letter that may not always be mapped:

* ``Heating Oil`` sheet — one row per supplier, one column per check-in date,
  values in **pence per litre ex-VAT**. These are converted to GBP/litre
  inclusive of 5% VAT before being recorded as ``spreadsheet``-sourced quotes.
* ``Brent crude`` sheet — monthly Brent spot price in **USD per barrel** (EIA
  data), recorded into the ``brent_crude`` table as a market-direction signal.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import xlrd

from oilwatch.db import Database
from oilwatch.pricing import DOMESTIC_VAT_RATE, apply_vat, inclusive_total, pence_to_pounds

# Normalised spreadsheet supplier name -> normalised DB supplier name, for the
# handful of names that don't collapse to the same token after whitespace/case
# stripping (e.g. "Rix Petrolium" -> "Rix", "oil fast" -> "Oilfast Insch").
_SPREADSHEET_ALIASES = {
    "rixpetrolium": "rix",
    "turriffuels": "turrifffuels",
    "oilfast": "oilfastinsch",
    "homefueldirect": "homefuelsdirect",
}


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _supplier_id_map(db: Database) -> dict[str, int]:
    """Map normalised supplier name -> DB supplier id."""
    return {
        _normalise(row["name"]): int(row["id"])
        for row in db.list_suppliers(include_inactive=True)
    }


def _serial_to_date(value: float, datemode: int) -> str:
    return xlrd.xldate_as_datetime(value, datemode).date().isoformat()


def import_heating_oil(db: Database, xls_path: Path, quantity_liters: int = 1000) -> int:
    """Import the ``Heating Oil`` sheet as quotes. Returns number of rows inserted."""
    book = xlrd.open_workbook(str(xls_path), on_demand=True)
    sheet = book.sheet_by_name("Heating Oil")

    # Header dates live in row 1, from column 3 onward.
    date_cols: list[tuple[int, str]] = []
    for col in range(3, sheet.ncols):
        header = sheet.cell(1, col).value
        if isinstance(header, (int, float)):
            date_cols.append((col, _serial_to_date(float(header), book.datemode)))

    id_map = _supplier_id_map(db)

    # Idempotency: don't duplicate points already imported from the sheet.
    existing = {
        (row["supplier_id"], row["observed_at"])
        for row in db.all_quotes()
        if row.get("source") == "spreadsheet"
    }

    inserted = 0
    for row_idx in range(2, sheet.nrows):
        name = str(sheet.cell(row_idx, 0).value).strip()
        if not name:
            continue
        key = _SPREADSHEET_ALIASES.get(_normalise(name), _normalise(name))
        supplier_id = id_map.get(key)
        if supplier_id is None:
            continue

        for col, day in date_cols:
            raw = sheet.cell(row_idx, col).value
            if not isinstance(raw, (int, float)) or float(raw) <= 0:
                continue  # '-' / '*' / blank / non-numeric
            if (supplier_id, day) in existing:
                continue

            price_per_liter = apply_vat(pence_to_pounds(float(raw)), DOMESTIC_VAT_RATE)
            db.record_quote(
                {
                    "supplier_id": supplier_id,
                    "observed_at": day,
                    "quantity_liters": quantity_liters,
                    "status": "ok",
                    "price_per_liter": price_per_liter,
                    "total_price": inclusive_total(price_per_liter, quantity_liters),
                    "currency": "GBP",
                    "source": "spreadsheet",
                    "notes": f"Imported from spreadsheet ({float(raw):.2f}p ex-VAT).",
                    "raw_payload": {"price_ex_vat_pence": float(raw)},
                }
            )
            inserted += 1

    return inserted


def import_brent(db: Database, xls_path: Path) -> int:
    """Import the ``Brent crude`` sheet into the brent_crude table."""
    book = xlrd.open_workbook(str(xls_path), on_demand=True)
    sheet = book.sheet_by_name("Brent crude")

    inserted = 0
    for row_idx in range(3, sheet.nrows):
        serial = sheet.cell(row_idx, 0).value
        price = sheet.cell(row_idx, 1).value
        if not isinstance(serial, (int, float)) or not isinstance(price, (int, float)):
            continue
        day = _serial_to_date(float(serial), book.datemode)
        if db.record_brent(day, float(price), "spreadsheet"):
            inserted += 1

    return inserted


def import_spreadsheet(db: Database, xls_path: Path, quantity_liters: int = 1000) -> dict[str, Any]:
    """Import both sheets; return a summary of what was added."""
    return {
        "heating_oil_quotes": import_heating_oil(db, xls_path, quantity_liters),
        "brent_points": import_brent(db, xls_path),
    }
