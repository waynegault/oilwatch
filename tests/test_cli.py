"""The CLI's argument surface and the mappings into the app.

These are the places a change can quietly alter behaviour - a flag that stops
defaulting, an option wired to the wrong keyword, a guard that stops guarding -
so each one asserts a contract rather than restating a one-line handler.
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from oilwatch.cli import HANDLERS, build_parser, main

COMMANDS = [
    ["init"],
    ["discover"],
    ["suppliers"],
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
    ["submit-requests", "--suppliers", "gleaner_oils,oilfast"],
    ["import-spreadsheet", "--path", "P:/Oil Prices.xls"],
    ["record-purchase", "Scottish Fuels", "--price-per-liter", "1.0894"],
]


class ParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()

    def test_the_documented_commands_all_parse(self) -> None:
        # The command surface is a contract with the README and the user's own
        # habits; a renamed flag or dropped subcommand should fail here.
        for argv in COMMANDS:
            with self.subTest(argv=argv):
                self.assertTrue(self.parser.parse_args(argv).command)

    def test_a_command_is_required(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parser.parse_args([])

    def test_browser_quoting_is_opt_in(self) -> None:
        self.assertFalse(self.parser.parse_args(["quote-all"]).browser)

    def test_the_dispatch_table_covers_every_documented_command(self) -> None:
        """The parser and the handler table are two lists that must agree.

        A command with no handler is a KeyError at the owner's terminal, and a
        handler with no command is unreachable code the suite would otherwise
        pass over.
        """
        self.assertEqual(set(HANDLERS), {argv[0] for argv in COMMANDS})


class DispatchTests(unittest.TestCase):
    def _run(self, argv: list[str]):
        app = MagicMock()
        app.suppliers.return_value = []
        out = io.StringIO()
        with (
            patch("oilwatch.cli.OilWatchApp", return_value=app),
            patch.object(sys, "argv", ["oilwatch", *argv]),
            contextlib.redirect_stdout(out),
        ):
            main()
        return app, out.getvalue()

    def test_every_read_only_command_reaches_the_app(self) -> None:
        """One pass over the commands that only talk to the service.

        What each one calls is pinned by the tests below; this is the sweep that
        catches a handler that has been left pointing at nothing.
        """
        for argv in (
            ["init"],
            ["discover"],
            ["suppliers", "--include-inactive"],
            ["cheapest"],
            ["status"],
            ["chart"],
            ["time-series"],
            ["quote", "2", "--postcode", "AB1 1AA"],
            ["quote-all"],
            ["purchases"],
            ["update-brent"],
            ["monitor-email"],
            ["import-spreadsheet", "--path", "P:/Oil Prices.xls"],
            ["phone-script"],
        ):
            with self.subTest(argv=argv):
                app, _ = self._run(argv)
                self.assertTrue(app.mock_calls, f"{argv[0]} reached nothing on the app")

    def test_quote_forwards_its_positional_id_and_both_options(self) -> None:
        app, _ = self._run(["quote", "2", "--postcode", "AB1 1AA", "--browser"])
        app.quote_supplier.assert_called_once_with(2, postcode="AB1 1AA", prefer_browser=True)

    def test_import_spreadsheet_passes_the_path_through(self) -> None:
        app, _ = self._run(["import-spreadsheet", "--path", "P:/Oil Prices.xls"])
        app.import_spreadsheet.assert_called_once_with("P:/Oil Prices.xls")

    def test_api_discover_prints_what_the_tool_found(self) -> None:
        run = AsyncMock(return_value={"total_requests": 3})
        out = io.StringIO()
        with (
            patch("oilwatch.cli.discover_supplier_api", new=run),
            patch.object(sys, "argv", ["oilwatch", "api-discover", "--url", "https://example.co.uk"]),
            contextlib.redirect_stdout(out),
        ):
            main()

        self.assertEqual(run.await_args.args[0], "https://example.co.uk")
        self.assertIn("total_requests", out.getvalue())

    def test_quote_all_forwards_the_postcode_and_the_browser_flag(self) -> None:
        app, _ = self._run(["quote-all", "--postcode", "AB21 0YA", "--browser"])
        app.quote_all.assert_called_once_with(postcode="AB21 0YA", prefer_browser=True)

    def test_record_purchase_forwards_the_price_and_the_code(self) -> None:
        app, _ = self._run(
            ["record-purchase", "Scottish Fuels", "--price-per-liter", "1.0894", "--code", "autumn25"]
        )
        args, kwargs = app.record_purchase.call_args
        self.assertEqual(args[0], "Scottish Fuels")
        self.assertEqual(kwargs["price_per_liter"], 1.0894)
        self.assertEqual(kwargs["code"], "autumn25")

    def test_api_discover_without_a_url_reports_instead_of_guessing(self) -> None:
        out = io.StringIO()
        with (
            patch("oilwatch.cli.OilWatchApp"),
            patch.object(sys, "argv", ["oilwatch", "api-discover"]),
            contextlib.redirect_stdout(out),
        ):
            main()

        self.assertIn("Error", out.getvalue())

    def test_login_rejects_a_supplier_it_has_no_url_for(self) -> None:
        out = io.StringIO()
        with (
            patch("oilwatch.browser_auth.BrowserAuth") as auth_cls,
            patch.object(sys, "argv", ["oilwatch", "login", "unknown_supplier"]),
            contextlib.redirect_stdout(out),
        ):
            main()

        auth_cls.assert_not_called()
        self.assertIn("Error", out.getvalue())

    def test_login_uses_the_suppliers_own_url_and_always_closes(self) -> None:
        auth = MagicMock()
        with (
            patch("oilwatch.browser_auth.BrowserAuth", return_value=auth) as auth_cls,
            patch.object(sys, "argv", ["oilwatch", "login", "scottish_fuels"]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            main()

        auth_cls.assert_called_once_with("scottish_fuels")
        self.assertIn("scottishfuels", auth.interactive_login.call_args.args[0])
        auth.close.assert_called_once()

    def test_submit_requests_splits_the_supplier_list_and_closes_the_browser(self) -> None:
        auth = MagicMock()
        submit = MagicMock(return_value=[])
        with (
            patch("oilwatch.browser_auth.BrowserAuth", return_value=auth),
            patch("oilwatch.form_submit.submit_all", new=submit),
            patch.object(sys, "argv", ["oilwatch", "submit-requests", "--suppliers", "gleaner_oils, oilfast"]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            main()

        self.assertEqual(submit.call_args.args[1], ["gleaner_oils", "oilfast"])
        self.assertEqual(submit.call_args.kwargs["postcode"], self._expected_postcode())
        auth.launch.assert_called_once_with(headless=True)
        auth.close.assert_called_once()

    @staticmethod
    def _expected_postcode() -> str:
        from oilwatch.identity import load_contact

        return load_contact().postcode

    def test_register_passes_headless_as_the_inverse_of_visible(self) -> None:
        register = AsyncMock(return_value=[])
        for argv, expected in ((["register"], True), (["register", "--visible"], False)):
            with self.subTest(argv=argv):
                with (
                    patch("oilwatch.cli.OilWatchApp"),
                    patch("oilwatch.cli.register_all", new=register),
                    patch.object(sys, "argv", ["oilwatch", *argv]),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    main()
                self.assertEqual(register.await_args.kwargs["headless"], expected)

    def test_schedule_passes_the_postcode_to_the_scheduler(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
