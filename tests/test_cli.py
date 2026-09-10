"""The CLI: argument parsing and dispatch onto the app.

The app is mocked, so these pin the command surface and the arguments each
command forwards, not the work behind them.
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from unittest.mock import MagicMock, patch

from oilwatch.cli import build_parser, main

KNOWN_COMMANDS = [
    ["init"],
    ["discover"],
    ["suppliers"],
    ["suppliers", "--include-inactive"],
    ["cheapest"],
    ["status"],
    ["chart"],
    ["time-series"],
    ["quote", "2"],
    ["quote-all", "--postcode", "AB21 0YA", "--browser"],
    ["purchases"],
    ["update-brent"],
    ["monitor-email"],
    ["login-email"],
]


class ParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()

    def test_every_known_command_parses(self) -> None:
        for argv in KNOWN_COMMANDS:
            with self.subTest(argv=argv):
                self.assertTrue(self.parser.parse_args(argv).command)

    def test_a_command_is_required(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parser.parse_args([])

    def test_flags_default_and_parse(self) -> None:
        self.assertFalse(self.parser.parse_args(["quote-all"]).browser)

        parsed = self.parser.parse_args(["quote-all", "--browser", "--postcode", "AB10 1AA"])
        self.assertTrue(parsed.browser)
        self.assertEqual(parsed.postcode, "AB10 1AA")


class DispatchTests(unittest.TestCase):
    def _run(self, argv: list[str]):
        app = MagicMock()
        out = io.StringIO()
        with (
            patch("oilwatch.cli.OilWatchApp", return_value=app),
            patch.object(sys, "argv", ["oilwatch", *argv]),
            contextlib.redirect_stdout(out),
        ):
            main()
        return app, out.getvalue()

    def test_cheapest_prints_the_result(self) -> None:
        app = MagicMock()
        app.cheapest.return_value = {"supplier": "Scottish Fuels", "price_per_liter": 1.09}
        out = io.StringIO()
        with (
            patch("oilwatch.cli.OilWatchApp", return_value=app),
            patch.object(sys, "argv", ["oilwatch", "cheapest"]),
            contextlib.redirect_stdout(out),
        ):
            main()

        app.cheapest.assert_called_once_with()
        self.assertIn('"price_per_liter": 1.09', out.getvalue())

    def test_suppliers_forwards_include_inactive(self) -> None:
        app, _ = self._run(["suppliers", "--include-inactive"])
        app.suppliers.assert_called_once_with(include_inactive=True)

    def test_quote_all_forwards_postcode_and_browser(self) -> None:
        app, _ = self._run(["quote-all", "--postcode", "AB21 0YA", "--browser"])
        app.quote_all.assert_called_once_with(postcode="AB21 0YA", prefer_browser=True)

    def test_chart_prints_the_path(self) -> None:
        app = MagicMock()
        app.chart.return_value = "/tmp/chart.png"
        out = io.StringIO()
        with (
            patch("oilwatch.cli.OilWatchApp", return_value=app),
            patch.object(sys, "argv", ["oilwatch", "chart"]),
            contextlib.redirect_stdout(out),
        ):
            main()

        self.assertIn("/tmp/chart.png", out.getvalue())

    def test_record_purchase_forwards_the_code(self) -> None:
        app, _ = self._run(
            ["record-purchase", "Scottish Fuels", "--price-per-liter", "1.0894", "--code", "autumn25"]
        )
        _, kwargs = app.record_purchase.call_args
        self.assertEqual(kwargs["code"], "autumn25")
        self.assertEqual(kwargs["price_per_liter"], 1.0894)


if __name__ == "__main__":
    unittest.main()
