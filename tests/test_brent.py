from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import xlrd

from oilwatch.brent import fetch_brent_series, update_brent
from oilwatch.db import Database


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

    def cell(self, row: int, col: int) -> FakeCell:
        return FakeCell(self._rows[row][col])


class FakeBook:
    datemode = 0

    def __init__(self, data1_rows):
        self._sheet = FakeSheet(data1_rows)

    def sheet_by_name(self, name: str) -> FakeSheet:
        assert name == "Data 1"
        return self._sheet


class FetchBrentSeriesTests(unittest.TestCase):
    def test_parses_data1_sheet(self) -> None:
        rows = [
            ["Back to Contents", "Data 1: Europe Brent Spot Price FOB (Dollars per Barrel)"],
            ["Sourcekey", "RBRTE"],
            ["Date", "Europe Brent Spot Price FOB (Dollars per Barrel)"],
            [_serial((2026, 3, 20)), 70.5],
            [_serial((2026, 3, 21)), 71.2],
        ]
        response = Mock()
        response.content = b"fake"

        with patch("oilwatch.brent.httpx.get", return_value=response) as get, patch(
            "oilwatch.brent.xlrd.open_workbook", return_value=FakeBook(rows)
        ):
            series = fetch_brent_series()

        get.assert_called_once()
        self.assertEqual(series, [("2026-03-20", 70.5), ("2026-03-21", 71.2)])


class UpdateBrentTests(unittest.TestCase):
    def test_inserts_only_new_points(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.sqlite")
            db.init_schema()
            db.record_brent("2026-03-20", 70.5, "spreadsheet")

            with patch(
                "oilwatch.brent.fetch_brent_series",
                return_value=[("2026-03-20", 70.5), ("2026-03-21", 71.2)],
            ):
                result = update_brent(db)

            self.assertEqual(result["brent_points_inserted"], 1)
            self.assertEqual(result["latest"], ("2026-03-21", 71.2))
            self.assertEqual(len(db.all_brent()), 2)


if __name__ == "__main__":
    unittest.main()
