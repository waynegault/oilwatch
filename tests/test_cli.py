"""The CLI: argument parsing and dispatch onto the app.

The app is mocked, so these pin the command surface and the arguments each
command forwards, not the work behind them.
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

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
    ["schedule", "--postcode", "AB21 0YA"],
    ["phone-script", "--quantity-liters", "900"],
    ["api-discover", "--url", "https://example.co.uk"],
    ["register"],
    ["login", "scottish_fuels"],
    ["submit-requests", "--suppliers", "gleaner_oils"],
    ["import-spreadsheet", "--path", "P:/Oil Prices.xls"],
    ["place-order", "2", "1.05", "--quantity-liters", "900"],
    ["record-purchase", "Scottish Fuels", "--price-per-liter", "1.0894"],
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

    def test_one_liner_commands_delegate(self) -> None:
        for argv, method in (
            ("init", "init"),
            ("discover", "discover_suppliers"),
            ("status", "status"),
            ("update-brent", "update_brent"),
            ("purchases", "purchases"),
            ("monitor-email", "monitor_email"),
        ):
            with self.subTest(argv=argv):
                app, _ = self._run([argv])
                getattr(app, method).assert_called_once()

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

    def test_time_series_prints_the_path(self) -> None:
        app = MagicMock()
        app.time_series_chart.return_value = "/tmp/ts.png"
        out = io.StringIO()
        with (
            patch("oilwatch.cli.OilWatchApp", return_value=app),
            patch.object(sys, "argv", ["oilwatch", "time-series"]),
            contextlib.redirect_stdout(out),
        ):
            main()

        self.assertIn("/tmp/ts.png", out.getvalue())

    def test_suppliers_forwards_include_inactive(self) -> None:
        app, _ = self._run(["suppliers", "--include-inactive"])
        app.suppliers.assert_called_once_with(include_inactive=True)

    def test_quote_forwards_supplier_and_browser(self) -> None:
        app, _ = self._run(["quote", "2", "--postcode", "AB21 0YA", "--browser"])
        app.quote_supplier.assert_called_once_with(2, postcode="AB21 0YA", prefer_browser=True)

    def test_quote_all_forwards_postcode_and_browser(self) -> None:
        app, _ = self._run(["quote-all", "--postcode", "AB21 0YA", "--browser"])
        app.quote_all.assert_called_once_with(postcode="AB21 0YA", prefer_browser=True)

    def test_import_spreadsheet_forwards_the_path(self) -> None:
        app, _ = self._run(["import-spreadsheet", "--path", "P:/Oil Prices.xls"])
        app.import_spreadsheet.assert_called_once_with("P:/Oil Prices.xls")

    def test_place_order_forwards_the_price_and_quantity(self) -> None:
        app, _ = self._run(["place-order", "2", "1.05", "--quantity-liters", "900"])
        _, kwargs = app.place_order.call_args
        self.assertEqual(kwargs["agreed_price_per_liter"], 1.05)
        self.assertEqual(kwargs["quantity_liters"], 900)

    def test_record_purchase_forwards_the_code(self) -> None:
        app, _ = self._run(
            ["record-purchase", "Scottish Fuels", "--price-per-liter", "1.0894", "--code", "autumn25"]
        )
        _, kwargs = app.record_purchase.call_args
        self.assertEqual(kwargs["code"], "autumn25")
        self.assertEqual(kwargs["price_per_liter"], 1.0894)

    def test_phone_script_lists_suppliers(self) -> None:
        app, _ = self._run(["phone-script"])
        app.suppliers.assert_called_once_with(include_inactive=False)

    def test_schedule_runs_the_scheduler(self) -> None:
        app = MagicMock()
        with (
            patch("oilwatch.cli.OilWatchApp", return_value=app),
            patch("oilwatch.cli.OilWatchScheduler") as scheduler,
            patch.object(sys, "argv", ["oilwatch", "schedule", "--postcode", "AB21 0YA"]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            main()

        scheduler.assert_called_once_with(app, postcode="AB21 0YA")
        scheduler.return_value.run_forever.assert_called_once_with()

    def test_api_discover_runs_the_discovery(self) -> None:
        discover = AsyncMock(return_value={"endpoints": []})
        with (
            patch("oilwatch.cli.OilWatchApp"),
            patch("oilwatch.cli.discover_supplier_api", new=discover),
            patch.object(sys, "argv", ["oilwatch", "api-discover", "--url", "https://example.co.uk"]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            main()

        self.assertEqual(discover.await_count, 1)

    def test_api_discover_without_a_url_reports_and_stops(self) -> None:
        out = io.StringIO()
        with (
            patch("oilwatch.cli.OilWatchApp"),
            patch.object(sys, "argv", ["oilwatch", "api-discover"]),
            contextlib.redirect_stdout(out),
        ):
            main()

        self.assertIn("Error", out.getvalue())

    def test_register_runs_every_supplier(self) -> None:
        register = AsyncMock(return_value=[])
        with (
            patch("oilwatch.cli.OilWatchApp"),
            patch("oilwatch.cli.register_all", new=register),
            patch.object(sys, "argv", ["oilwatch", "register"]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            main()

        self.assertEqual(register.await_count, 1)

    def test_login_signs_in_and_closes(self) -> None:
        auth = MagicMock()
        with (
            patch("oilwatch.browser_auth.BrowserAuth", return_value=auth),
            patch.object(sys, "argv", ["oilwatch", "login", "scottish_fuels"]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            main()

        auth.interactive_login.assert_called_once()
        auth.close.assert_called_once()

    def test_login_without_a_known_supplier_reports(self) -> None:
        out = io.StringIO()
        with (
            patch("oilwatch.browser_auth.BrowserAuth") as auth_cls,
            patch.object(sys, "argv", ["oilwatch", "login", "unknown_supplier"]),
            contextlib.redirect_stdout(out),
        ):
            main()

        auth_cls.assert_not_called()
        self.assertIn("Error", out.getvalue())

    def test_submit_requests_drives_the_forms(self) -> None:
        auth = MagicMock()
        submit = MagicMock(return_value=[])
        with (
            patch("oilwatch.browser_auth.BrowserAuth", return_value=auth),
            patch("oilwatch.form_submit.submit_all", new=submit),
            patch.object(sys, "argv", ["oilwatch", "submit-requests", "--suppliers", "gleaner_oils"]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            main()

        submit.assert_called_once()
        auth.launch.assert_called_once_with(headless=True)
        auth.close.assert_called_once()

    def test_login_email_reports_success(self) -> None:
        monitor = MagicMock()
        monitor.interactive_login.return_value = {"access_token": "token"}
        out = io.StringIO()
        with (
            patch("oilwatch.graph_email.GraphEmailMonitor", return_value=monitor),
            patch.object(sys, "argv", ["oilwatch", "login-email"]),
            contextlib.redirect_stdout(out),
        ):
            main()

        self.assertIn("successful", out.getvalue())


if __name__ == "__main__":
    unittest.main()
