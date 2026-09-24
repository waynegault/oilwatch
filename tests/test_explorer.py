"""The explorer page's row-ordering rule.

The page is one HTML file with the data embedded and its tables built in the
browser, so the part of it worth pinning in Python is the order the current-window
rows are *meant* to appear in: the rule that keeps the table from disagreeing with
the winner named above it. Getting it wrong is silent — the page still renders,
with the wrong row at the top.

`tools/` scripts are verified by running them (`build_snapshot.py` checks its own
output, `build_explorer.py` writes the page and runs `node --check` on it), which
is why this file loads the tool by path rather than importing it as a package.
`test_snapshot.py` does the same for the snapshot tool's staleness report.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_build_explorer():
    spec = importlib.util.spec_from_file_location(
        "build_explorer", ROOT / "tools" / "build_explorer.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_explorer = _load_build_explorer()


def quote(name: str, price: float, *, effective: float | None = None, kind: str = "supplier") -> dict:
    return {
        "name": name,
        "price_per_liter": price,
        "effective_price_per_liter": effective,
        "kind": kind,
    }


def names(rows: list[dict]) -> list[str]:
    return [row["name"] for row in rows]


class RankForDisplayTests(unittest.TestCase):
    def test_a_discount_can_move_a_supplier_to_the_top(self) -> None:
        """The ranking is the winner's basis, so a code has to reorder the rows.

        The rows arrive in headline order — `db.latest_quotes` orders on
        ``price_per_liter`` — while the winner is chosen on the effective price, so
        a supplier whose code undercuts another's headline used to sit below it
        with the winner card above naming a different supplier.
        """
        rows = [
            quote("HomeFuels Direct", 1.1761),
            quote("ValueOils", 1.2000, effective=1.1700),
        ]

        self.assertEqual(names(build_explorer.rank_for_display(rows)), ["ValueOils", "HomeFuels Direct"])

    def test_a_benchmark_sorts_last_however_cheap(self) -> None:
        """Fueltool's UK average is usually below every real quote.

        A table whose first row is a figure rather than a supplier is the
        misreading `kind` exists to prevent, and the benchmark was in fact the
        first row of the page built on 2026-09-24 (1.1745 against a 1.1761
        winner).
        """
        rows = [
            quote("HomeFuels Direct", 1.1761),
            quote("Fueltool", 1.1745, kind="benchmark"),
            quote("Rix", 1.3267),
        ]

        self.assertEqual(
            names(build_explorer.rank_for_display(rows)),
            ["HomeFuels Direct", "Rix", "Fueltool"],
        )

    def test_a_row_with_no_code_ranks_on_its_headline(self) -> None:
        rows = [
            quote("B", 1.20, effective=1.05),
            quote("A", 1.10),
            quote("C", 1.15, effective=None),
        ]

        self.assertEqual(names(build_explorer.rank_for_display(rows)), ["B", "A", "C"])


if __name__ == "__main__":
    unittest.main()
