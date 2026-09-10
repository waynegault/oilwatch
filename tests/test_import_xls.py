from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import xlrd

from oilwatch.db import Database
from oilwatch.import_xls import import_brent, import_heating_oil
from oilwatch.pricing import apply_vat, pence_to_pounds


def _serial(day: tuple[int, int, int]) -> float:
    return float(xlrd.xldate.xldate_from_date_tuple(day, 0))


class FakeCell:
    def __init__(self, value):
        self.value = value


class FakeSheet:
    def __init__(self, rows):
        self._rows = rows

    @property
    def nrows(self) -> int:
        return len(self._rows)

    @property
    def ncols(self) -> int:
        return max((len(r) for r in self._rows), default=0)

    def cell(self, row: int, col: int):
        r = self._rows[row]
        return FakeCell(r[col] if col < len(r) else "")


class FakeBook:
    datemode = 0

    def __init__(self, sheets):
        self._sheets = sheets

    def sheet_by_name(self, name: str) -> FakeSheet:
        return self._sheets[name]


def _add_supplier(db: Database, name: str, website: str) -> int:
    return db.upsert_supplier(
        {
            "name": name,
            "website": website,
            "status": "active",
            "connector_type": "manual",
            "connector_config": {},
        }
    )


class ImportHeatingOilTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.sqlite")
        self.db.init_schema()
        self.connon = _add_supplier(self.db, "Connon Bros", "https://connon.fuelsoft.co.uk/")
        self.rix = _add_supplier(self.db, "Rix", "https://www.rix.co.uk/")
        self.oilfast = _add_supplier(self.db, "Oilfast Insch", "https://oilfast.co.uk/depot/insch/")
        self.homefuels = _add_supplier(self.db, "HomeFuels Direct", "https://homefuelsdirect.co.uk/")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _heating_sheet(self) -> FakeBook:
        d1, d2 = _serial((2026, 3, 20)), _serial((2026, 3, 21))
        rows = [
            ["", "", "", "Price per liter ex VAT (pence)"],
            ["Company", "Web", "Tel", d1, d2],
            ["Connon bros ", "https://connon.fuelsoft.co.uk/", "tel", 27.34, 35.69],
            ["Rix Petrolium", "https://www.rix.co.uk/", "tel", "-", 50.0],
            ["oil fast", "", "tel", 40.0, 41.0],
            ["Home fuel direct", "https://homefuelsdirect.co.uk/", "tel", 68.99, ""],
        ]
        return FakeBook({"Heating Oil": FakeSheet(rows)})

    def test_imports_mapped_suppliers_and_normalises_vat(self) -> None:
        with patch("oilwatch.import_xls.xlrd.open_workbook", return_value=self._heating_sheet()):
            inserted = import_heating_oil(self.db, Path("unused.xls"))

        # Connon 2, Rix 1 ('-' skipped), Oilfast 2, HomeFuels 1 (blank skipped).
        self.assertEqual(inserted, 6)

        quotes = self.db.all_quotes()
        by_supplier: dict[int, list[dict]] = {}
        for q in quotes:
            by_supplier.setdefault(q["supplier_id"], []).append(q)

        connon = sorted(by_supplier[self.connon], key=lambda q: q["observed_at"])
        self.assertEqual(connon[0]["price_per_liter"], apply_vat(pence_to_pounds(27.34)))
        self.assertEqual(connon[1]["price_per_liter"], apply_vat(pence_to_pounds(35.69)))
        self.assertTrue(all(q["source"] == "spreadsheet" for q in quotes))

        # 'rix petrolium' -> 'Rix' alias mapping works.
        rix = by_supplier[self.rix]
        self.assertEqual(len(rix), 1)
        self.assertEqual(rix[0]["price_per_liter"], apply_vat(pence_to_pounds(50.0)))

        # 'oil fast' -> 'Oilfast Insch' alias mapping works.
        self.assertEqual(len(by_supplier[self.oilfast]), 2)

    def test_import_is_idempotent(self) -> None:
        with patch("oilwatch.import_xls.xlrd.open_workbook", return_value=self._heating_sheet()):
            first = import_heating_oil(self.db, Path("unused.xls"))
            second = import_heating_oil(self.db, Path("unused.xls"))
        self.assertEqual(first, 6)
        self.assertEqual(second, 0)


class ImportBrentTests(unittest.TestCase):
    def test_imports_brent_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.sqlite")
            db.init_schema()

            rows = [
                ["https://view.officeapps.live.com/..."],
                [""],
                ["Date", "Brent crude $"],
                [_serial((2026, 3, 20)), 70.5],
                [_serial((2026, 3, 21)), 71.2],
            ]
            book = FakeBook({"Brent crude": FakeSheet(rows)})
            with patch("oilwatch.import_xls.xlrd.open_workbook", return_value=book):
                inserted = import_brent(db, Path("unused.xls"))

            self.assertEqual(inserted, 2)
            brent = db.all_brent()
            self.assertEqual([r["observed_at"] for r in brent], ["2026-03-20", "2026-03-21"])
            self.assertEqual(brent[0]["price_usd_per_barrel"], 70.5)
            self.assertTrue(all(r["source"] == "spreadsheet" for r in brent))


if __name__ == "__main__":
    unittest.main()
