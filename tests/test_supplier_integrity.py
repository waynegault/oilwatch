"""The duplicate-supplier check: what counts as a pair, and what must not.

The regression this guards is the Turriff Fuels twin of 2026-09-22. A register
entry was repointed at a new host, ``upsert_supplier`` keyed on ``website``
found no match, and a second row appeared beside the first. Both rows were
``active`` and shared the name, so nothing downstream looked wrong while quote
history sat split across two suppliers.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from oilwatch.db import Database
from oilwatch.supplier_integrity import (
    duplicate_supplier_groups,
    normalise_email,
    normalise_name,
)


class NormaliseNameTests(unittest.TestCase):
    def test_folds_case_punctuation_and_whitespace(self) -> None:
        self.assertEqual(normalise_name("Turriff Fuels"), normalise_name("TURRIFF-FUELS"))
        self.assertEqual(normalise_name("Turriff Fuels"), normalise_name("  Turriff   Fuels  "))
        self.assertEqual(normalise_name("Crown Oil Ltd."), normalise_name("crown oil ltd"))

    def test_keeps_genuinely_different_names_apart(self) -> None:
        # A trading suffix is not noise: folding it away would merge companies.
        self.assertNotEqual(normalise_name("Crown Oil"), normalise_name("Crown Oil Ltd"))
        self.assertNotEqual(normalise_name("Scottish Fuels"), normalise_name("Scottish Power"))

    def test_missing_name_is_empty_not_a_match(self) -> None:
        self.assertEqual(normalise_name(None), "")
        self.assertEqual(normalise_name(""), "")


class NormaliseEmailTests(unittest.TestCase):
    def test_folds_case_and_surrounding_space(self) -> None:
        self.assertEqual(normalise_email(" Info@Example.com "), normalise_email("info@example.com"))

    def test_missing_email_is_empty(self) -> None:
        self.assertEqual(normalise_email(None), "")


def _supplier(supplier_id: int, name: str, website: str, **overrides: object) -> dict:
    record: dict = {
        "id": supplier_id,
        "name": name,
        "website": website,
        "status": "active",
        "email": "",
        "last_seen_at": "2026-09-22 15:03:59",
    }
    record.update(overrides)
    return record


class DuplicateSupplierGroupsTests(unittest.TestCase):
    def test_same_name_on_two_hosts_is_reported(self) -> None:
        """The Turriff shape: one supplier, two websites, two rows."""
        groups = duplicate_supplier_groups(
            [
                _supplier(193, "Turriff Fuels", "https://www.turrifffuels.com/"),
                _supplier(52, "Turriff Fuels", "https://turriff-fuels.co.uk"),
            ]
        )
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["reason"], "name")
        self.assertEqual(groups[0]["key"], "turrifffuels")
        self.assertEqual([row["id"] for row in groups[0]["suppliers"]], [52, 193])

    def test_same_email_is_reported_even_when_names_differ(self) -> None:
        groups = duplicate_supplier_groups(
            [
                _supplier(7, "Carnegie Fuels", "https://a.example.com", email="info@c.example.com"),
                _supplier(8, "Carnegie Fuel Services", "https://b.example.com", email="INFO@c.example.com"),
            ]
        )
        self.assertEqual([group["reason"] for group in groups], ["email"])
        self.assertEqual(groups[0]["key"], "info@c.example.com")

    def test_distinct_suppliers_are_not_reported(self) -> None:
        groups = duplicate_supplier_groups(
            [
                _supplier(1, "Crown Oil", "https://crown.example.com", email="a@crown.example.com"),
                _supplier(2, "Scottish Fuels", "https://scottish.example.com", email="b@s.example.com"),
            ]
        )
        self.assertEqual(groups, [])

    def test_rows_without_a_name_or_email_are_skipped(self) -> None:
        """Two nameless rows are two absences, not a pair."""
        groups = duplicate_supplier_groups(
            [
                _supplier(1, "", "https://a.example.com"),
                _supplier(2, "", "https://b.example.com"),
            ]
        )
        self.assertEqual(groups, [])

    def test_a_supplier_matching_on_both_signals_is_grouped_once_per_reason(self) -> None:
        groups = duplicate_supplier_groups(
            [
                _supplier(1, "Nexus Oils", "https://a.example.com", email="x@nexus.example.com"),
                _supplier(2, "nexus-oils", "https://b.example.com", email="x@nexus.example.com"),
            ]
        )
        self.assertEqual([group["reason"] for group in groups], ["name", "email"])

    def test_three_rows_with_one_name_are_one_group(self) -> None:
        groups = duplicate_supplier_groups(
            [
                _supplier(1, "Rix", "https://a.example.com"),
                _supplier(2, "RIX", "https://b.example.com"),
                _supplier(3, "Rix.", "https://c.example.com"),
            ]
        )
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["suppliers"]), 3)


class DatabaseDuplicateSupplierTests(unittest.TestCase):
    """The check read back from a real database, since that is where the twin was."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.sqlite")
        self.db.init_schema()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _record(self, name: str, website: str, **overrides: object) -> dict:
        record = {"name": name, "website": website, "status": "active", "connector_config": {}}
        record.update(overrides)
        return record

    def test_a_changed_website_creates_a_twin_the_check_reports(self) -> None:
        """Reproduces the fault: the upsert cannot see that these are one supplier."""
        self.db.upsert_supplier(self._record("Turriff Fuels", "https://turriff-fuels.co.uk"))
        self.db.upsert_supplier(self._record("Turriff Fuels", "https://www.turrifffuels.com/"))

        # Two rows, because the conflict target is the website and it changed.
        self.assertEqual(len(self.db.list_suppliers()), 2)

        groups = self.db.find_duplicate_suppliers()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["key"], "turrifffuels")
        self.assertEqual(len(groups[0]["suppliers"]), 2)

    def test_repeated_upsert_of_one_website_is_not_a_duplicate(self) -> None:
        self.db.upsert_supplier(self._record("Turriff Fuels", "https://www.turrifffuels.com/"))
        self.db.upsert_supplier(
            self._record("Turriff Fuels", "https://www.turrifffuels.com/", phone="01224 123456")
        )
        self.assertEqual(self.db.find_duplicate_suppliers(), [])

    def test_an_inactive_twin_is_hidden_by_default_and_shown_on_request(self) -> None:
        self.db.upsert_supplier(self._record("Turriff Fuels", "https://turriff-fuels.co.uk"))
        self.db.mark_missing_suppliers_inactive(["https://www.turrifffuels.com/"])
        self.db.upsert_supplier(self._record("Turriff Fuels", "https://www.turrifffuels.com/"))

        self.assertEqual(self.db.find_duplicate_suppliers(), [])
        self.assertEqual(len(self.db.find_duplicate_suppliers(include_inactive=True)), 1)


if __name__ == "__main__":
    unittest.main()
