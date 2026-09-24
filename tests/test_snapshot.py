"""The snapshot tool's staleness report.

``data/oilwatch-history.sqlite`` is a copy of the live database taken by hand, so
it goes stale whenever the register or the database moves and nothing else
notices. ``--check`` is what says so, and this pins the two ways it has to say
it: rows that are missing, and rows whose *values* changed while every count
stayed the same — which is what ValueOils' corrected phone number was, and why
two rebuilds went unmade on 2026-09-24.

Like ``test_explorer.py``, this loads the tool by path: ``tools/`` is not a
package, and its scripts are otherwise verified by running them.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from oilwatch.db import Database

ROOT = Path(__file__).resolve().parents[1]


def _load_build_snapshot():
    spec = importlib.util.spec_from_file_location(
        "build_snapshot", ROOT / "tools" / "build_snapshot.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_snapshot = _load_build_snapshot()

SUPPLIER = {
    "name": "ValueOils",
    "website": "https://valueoils.example.com",
    "status": "active",
    "phone": "006 03300 57 08 57",
    "connector_type": "manual",
    "connector_config": {},
}


@unittest.skipUnless(
    (ROOT / "config" / "contact.json").exists() and (ROOT / "config" / "settings.json").exists(),
    "the export redacts the owner's own details, read from config files a fresh clone has not got",
)
class CommittedExportDriftTests(unittest.TestCase):
    """``--check`` has to tell "in step" from both kinds of drift."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.live_path = root / "live.sqlite"
        self.committed_path = root / "committed.sqlite"

        self.live = Database(self.live_path)
        self.live.init_schema()
        self.supplier_id = self.live.upsert_supplier(dict(SUPPLIER))

        # A committed export, built the real way from this live database. The
        # paths are passed rather than patched into the module: the tool takes
        # them as parameters for exactly this reason.
        build_snapshot.build(self.live_path, self.committed_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def drift(self) -> list[str]:
        return build_snapshot.committed_export_drift(self.live_path, self.committed_path)

    def test_an_export_that_matches_the_database_says_so(self) -> None:
        lines = self.drift()

        self.assertEqual(len(lines), 1, lines)
        self.assertIn("in step with the live database", lines[0])

    def test_a_corrected_value_is_drift_even_though_no_row_moved(self) -> None:
        """The case that motivated the digest: same counts, different value."""
        self.live.upsert_supplier({**SUPPLIER, "phone": "03300 570 857"})

        lines = self.drift()

        self.assertIn("BEHIND", lines[0])
        self.assertTrue(
            any("different values in: suppliers" in line for line in lines),
            lines,
        )

    def test_a_new_row_is_reported_as_rows_behind(self) -> None:
        self.live.record_quote(
            {
                "supplier_id": self.supplier_id,
                "observed_at": "2026-09-24T14:40:32.626682",
                "quantity_liters": 1000,
                "status": "ok",
                "price_per_liter": 1.1907,
                "total_price": 1190.7,
                "currency": "GBP",
                "source": "valueoils_browser",
                "notes": "",
                "raw_payload": {},
            }
        )

        lines = self.drift()

        self.assertIn("BEHIND", lines[0])
        self.assertTrue(any("behind by 1 row(s)" in line for line in lines), lines)
        self.assertTrue(any("quotes: 0 committed, 1 would be written" in line for line in lines), lines)

    def test_an_absent_export_is_stated_rather_than_compared(self) -> None:
        self.committed_path.unlink()

        lines = self.drift()

        self.assertEqual(len(lines), 1, lines)
        self.assertIn("nothing to compare", lines[0])


if __name__ == "__main__":
    unittest.main()
