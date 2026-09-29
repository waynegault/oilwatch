"""The calibration tool's measurement, including the case where it measures nothing.

``tools/fuel_mail_calibration.py`` answers whether the fuel-mail threshold
separates fuel senders from everyone else, using the verdicts the sweep has
already stored and the labels the tracked register supplies. Two of its
properties are worth pinning: that a sample which cannot constrain the threshold
says so rather than reading as a pass — an unexercised threshold is the state this
install is actually in — and that it never prints a correspondent the register
does not name, because ``sender_judgements`` is one row per sender the sweep could
not place and that is the correspondence ``tools/build_snapshot.py`` exists to keep
out of the repository.

Like ``test_snapshot.py``, this loads the tool by path: ``tools/`` is not a
package, and its scripts are otherwise verified by running them.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_calibration():
    spec = importlib.util.spec_from_file_location(
        "fuel_mail_calibration", ROOT / "tools" / "fuel_mail_calibration.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before execution because the tool declares a ``@dataclass``, and
    # ``dataclasses`` resolves the class's own module out of ``sys.modules`` while
    # processing it - a module that is only in the loader's hands is ``None``
    # there, and the import dies with an AttributeError from inside dataclasses.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


calibration = _load_calibration()

REGISTER = {
    "suppliers": [
        {"name": "Valley Fuels", "website": "https://www.valleyfuels.example/"},
        {"name": "Glen Oils", "website": "https://glenoils.example/quote"},
    ],
    "excluded_domains": ["sitedirectory.example"],
}


class _ToolTestCase(unittest.TestCase):
    """A temp database and a temp register: the live ones are the owner's."""

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
        self.register = self.tmp / "suppliers.json"
        self.register.write_text(json.dumps(REGISTER), encoding="utf-8")
        self.database = self.tmp / "oilwatch.sqlite"

    def _seed(self, rows: list[tuple[str, float]]) -> None:
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sender_judgements ("
                "domain TEXT PRIMARY KEY, fuel_probability REAL NOT NULL, "
                "judged_at TEXT, covers_through TEXT)"
            )
            connection.executemany(
                "INSERT OR REPLACE INTO sender_judgements "
                "(domain, fuel_probability, judged_at) VALUES (?, ?, '2026-09-29T00:00:00')",
                rows,
            )
            connection.commit()
        finally:
            connection.close()

    def _run(self, threshold: float = 0.8, *, check: bool = False) -> tuple[int, str]:
        argv = [
            "--database",
            str(self.database),
            "--register",
            str(self.register),
            "--threshold",
            str(threshold),
        ]
        if check:
            argv.append("--check")
        captured = io.StringIO()
        with redirect_stdout(captured):
            code = calibration.main(argv)
        return code, captured.getvalue()


class MeasurementTests(_ToolTestCase):
    def test_an_unexercised_threshold_is_unconstrained_and_not_a_pass(self) -> None:
        """The state this install is in: 156 verdicts, none within 0.2 of 0.8.

        Nothing is contradicted and nothing is validated, and those must not read
        alike — a check that printed "ok" here would be reporting the absence of
        evidence as evidence.
        """
        self._seed([("mailer.example", 0.01), ("shop.example", 0.03), ("bank.example", 0.05)])

        code, out = self._run(check=True)

        self.assertEqual(code, 0, "an unconstrained sample is not a failure, but it is not a pass")
        self.assertIn("UNCONSTRAINED", out)
        self.assertIn("not a pass", out)
        self.assertIn("0 of 3", out)

    def test_an_excluded_domain_at_or_above_the_threshold_is_a_contradiction(self) -> None:
        """The alert firing on a directory site is the fault the threshold prevents."""
        self._seed([("sitedirectory.example", 0.91)])

        code, out = self._run(check=True)

        self.assertEqual(code, 1)
        self.assertIn("CONTRADICTED", out)
        self.assertIn("sitedirectory.example 0.91", out)
        self.assertIn("excluded, but would be named", out)

    def test_a_register_supplier_below_the_threshold_is_a_contradiction(self) -> None:
        """A supplier read as not-fuel is the miss the judgement was written to stop.

        The address is a subdomain of the register's website, which is the shape a
        real sender arrives in: the register records where to order, the message
        arrives from ``email.`` or ``noreply.`` at the same company.
        """
        self._seed([("email.valleyfuels.example", 0.12)])

        code, out = self._run(check=True)

        self.assertEqual(code, 1)
        self.assertIn("CONTRADICTED", out)
        self.assertIn("a register supplier, not named", out)

    def test_a_sample_that_sits_near_the_threshold_with_the_right_labels_is_consistent(
        self,
    ) -> None:
        self._seed([("valleyfuels.example", 0.9), ("sitedirectory.example", 0.1)])

        code, out = self._run(check=True)

        self.assertEqual(code, 0)
        self.assertIn("CONSISTENT", out)
        self.assertIn("2 verdict(s) carry a register label", out)

    def test_without_check_a_contradiction_is_reported_but_not_fatal(self) -> None:
        """The plain run is a report: it is read, not wired into anything."""
        self._seed([("sitedirectory.example", 0.95)])

        code, out = self._run(check=False)

        self.assertEqual(code, 0)
        self.assertIn("CONTRADICTED", out)

    def test_an_unlabelled_correspondent_is_never_printed(self) -> None:
        """The privacy line: only the register's own names reach the terminal."""
        self._seed(
            [
                ("a-personal-friend.example", 0.01),
                ("sitedirectory.example", 0.95),
            ]
        )

        code, out = self._run(check=True)

        self.assertEqual(code, 1)
        self.assertIn("sitedirectory.example", out)
        self.assertNotIn("a-personal-friend.example", out)

    def test_a_missing_database_is_reported_rather_than_raised(self) -> None:
        code, out = self._run()

        self.assertEqual(code, 0)
        self.assertIn("does not exist", out)


class LabelTests(unittest.TestCase):
    """The register's labels, and the looseness that joins them to a sender domain."""

    def test_register_labels_strip_the_scheme_path_and_www(self) -> None:
        suppliers, excluded = calibration.register_labels(REGISTER)

        self.assertEqual(suppliers, {"valleyfuels.example", "glenoils.example"})
        self.assertEqual(excluded, {"sitedirectory.example"})

    def test_a_supplier_is_recognised_however_the_website_and_the_sender_are_spelled(self) -> None:
        suppliers, _ = calibration.register_labels(REGISTER)

        for domain in (
            "valleyfuels.example",
            "www.valleyfuels.example",
            "email.valleyfuels.example",
            "noreply.www.valleyfuels.example",
        ):
            with self.subTest(domain=domain):
                self.assertIsNotNone(calibration._labelled_as(domain, suppliers))

    def test_a_domain_the_register_does_not_name_carries_no_label(self) -> None:
        suppliers, excluded = calibration.register_labels(REGISTER)

        for domain in ("other.example", "valleyfuels.example.net", "", "example"):
            with self.subTest(domain=domain):
                self.assertIsNone(calibration._labelled_as(domain, suppliers))
                self.assertIsNone(calibration._labelled_as(domain, excluded))

    def test_a_verdict_that_sits_exactly_on_the_threshold_is_named(self) -> None:
        """``>=`` is the caller's rule, so the report has to use the same one."""
        result = calibration.calibrate([("x.example", 0.8)], 0.8, {"x.example"}, set())

        self.assertEqual(result.at_or_above, 1)
        self.assertEqual(result.supplier_at_or_above, [("x.example", 0.8)])


if __name__ == "__main__":
    unittest.main()
