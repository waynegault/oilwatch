"""The handler paths the rest of the suite leaves unexercised.

Covers: `phone-script`'s call-sheet loop and its `--output` export, `api-discover`
resolving a URL from `--supplier-id` (including a bad id), `register`'s summary
and its `--output` save, and `login-email`'s success/failure reporting.

These are the six regions `cli_handlers.py` was still missing coverage on, and
each is a contract rather than a restatement of a one-line handler: a call sheet
that exported nothing, a supplier id that discovered against the wrong site, a
registration summary that dropped a supplier, or an authentication failure
reported as "token cached" are all failures the owner would only meet at the
terminal. The one-line delegations that remain uncovered are deliberately so —
see the note at the top of test_mcp_server.py.

The suite is plain ``unittest``, offline: every heavy call (the registrar, the
API discovery, the Graph client) is patched, and anything that writes goes to a
temporary directory.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

from oilwatch.cli import main

# ── Shared fixtures ────────────────────────────────────────────────────


def run_cli(argv: list[str], app: MagicMock | None = None) -> tuple[MagicMock, str]:
    """Dispatch ``argv`` through the real parser and dispatch table.

    Going through ``main()`` rather than calling a handler directly keeps the
    parser, the handler table and the handler itself in one piece, so a renamed
    flag or a mis-wired option fails here too.
    """
    app = app if app is not None else MagicMock()
    out = io.StringIO()
    with (
        patch("oilwatch.cli.OilWatchApp", return_value=app),
        patch.object(sys, "argv", ["oilwatch", *argv]),
        contextlib.redirect_stdout(out),
    ):
        main()
    return app, out.getvalue()


def app_with_suppliers(names: list[str]) -> MagicMock:
    app = MagicMock()
    app.suppliers.return_value = [
        {"id": index, "name": name} for index, name in enumerate(names, start=1)
    ]
    return app


# ── phone-script ───────────────────────────────────────────────────────


class PhoneScriptOutputTests(unittest.TestCase):
    """The call sheet is what the owner reads from while on the phone."""

    def _script(self) -> MagicMock:
        script = MagicMock()
        script.generate_script.side_effect = lambda supplier: f"SCRIPT FOR {supplier['name']}"
        return script

    def test_every_supplier_gets_a_script(self) -> None:
        """A sheet that stopped at the first supplier would read as complete."""
        app = app_with_suppliers(["Rix", "Turriff Fuels"])
        script = self._script()
        with patch("oilwatch.cli_handlers.get_telephone_script", return_value=script):
            _, out = run_cli(["phone-script"], app)

        self.assertEqual(
            [call.args[0]["name"] for call in script.generate_script.call_args_list],
            ["Rix", "Turriff Fuels"],
        )
        self.assertIn("SCRIPT FOR Rix", out)
        self.assertIn("SCRIPT FOR Turriff Fuels", out)

    def test_output_writes_the_call_sheet_to_the_path_given(self) -> None:
        app = app_with_suppliers(["Rix"])
        script = self._script()
        handed: dict[str, object] = {}

        def export(suppliers, path: Path) -> Path:
            handed["suppliers"] = suppliers
            path.write_text("the call sheet", encoding="utf-8")
            return path

        script.export_to_json.side_effect = export

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "callsheet.json"
            with patch("oilwatch.cli_handlers.get_telephone_script", return_value=script):
                _, out = run_cli(["phone-script", "--output", str(target)], app)

            self.assertTrue(target.exists(), "--output did not write the call sheet")
            self.assertEqual(target.read_text(encoding="utf-8"), "the call sheet")
            self.assertEqual(handed["suppliers"], app.suppliers.return_value)

        self.assertIn("Call sheet exported to", out)

    def test_without_output_nothing_is_written(self) -> None:
        app = app_with_suppliers(["Rix"])
        script = self._script()
        with patch("oilwatch.cli_handlers.get_telephone_script", return_value=script):
            _, out = run_cli(["phone-script"], app)

        script.export_to_json.assert_not_called()
        self.assertNotIn("exported to", out)


# ── api-discover ───────────────────────────────────────────────────────


class SupplierApiDiscoveryTests(unittest.TestCase):
    """`--supplier-id` must discover against that supplier's own website."""

    def test_a_supplier_id_resolves_to_that_suppliers_website(self) -> None:
        app = MagicMock()
        app.db.get_supplier.return_value = {"id": 7, "website": "https://www.rix.co.uk"}
        discover = AsyncMock(return_value={"total_requests": 1})

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.json"
            with patch("oilwatch.cli_handlers.discover_supplier_api", discover):
                _, out = run_cli(
                    ["api-discover", "--supplier-id", "7", "--output", str(target)], app
                )

        self.assertEqual(discover.call_args.args[0], "https://www.rix.co.uk")
        self.assertIn("Discovering APIs on: https://www.rix.co.uk", out)

    def test_an_unknown_supplier_id_reports_instead_of_discovering_nothing(self) -> None:
        app = MagicMock()
        app.db.get_supplier.return_value = None
        discover = AsyncMock(return_value={})

        with patch("oilwatch.cli_handlers.discover_supplier_api", discover):
            _, out = run_cli(["api-discover", "--supplier-id", "999"], app)

        self.assertIn("Error: Please provide --url or --supplier-id", out)
        discover.assert_not_called()

    def test_the_default_output_file_is_named_after_the_url(self) -> None:
        app = MagicMock()
        app.db.get_supplier.return_value = {"id": 7, "website": "https://www.rix.co.uk/quote"}
        discover = AsyncMock(return_value={})

        with patch("oilwatch.cli_handlers.discover_supplier_api", discover):
            run_cli(["api-discover", "--supplier-id", "7"], app)

        self.assertEqual(
            discover.call_args.args[1], "api_discovery_www.rix.co.uk_quote.json"
        )

    def test_an_explicit_url_wins_over_the_supplier_row(self) -> None:
        app = MagicMock()
        app.db.get_supplier.return_value = {"id": 7, "website": "https://www.rix.co.uk"}
        discover = AsyncMock(return_value={})

        with patch("oilwatch.cli_handlers.discover_supplier_api", discover):
            run_cli(
                ["api-discover", "--url", "https://example.co.uk", "--supplier-id", "7"], app
            )

        self.assertEqual(discover.call_args.args[0], "https://example.co.uk")


# ── register ───────────────────────────────────────────────────────────


class RegistrationSummaryTests(unittest.TestCase):
    """Registration is a long, semi-manual run; its report must be complete."""

    RESULTS: ClassVar[list[dict[str, str]]] = [
        {
            "supplier": "Rix",
            "status": "registered",
            "email": "owner@example.test",
            "message": "confirmation email sent",
        },
        {
            "supplier": "BoilerJuice",
            "status": "failed",
            "email": "owner@example.test",
            "message": "no registration form on the page",
        },
    ]

    def _run_register(self, argv: list[str]) -> tuple[MagicMock, str]:
        app = MagicMock()
        app.settings.home.label = "Hatton of Fintry"
        register = AsyncMock(return_value=self.RESULTS)
        with patch("oilwatch.cli_handlers.register_all", register):
            app, out = run_cli(argv, app)
        return app, out

    def test_every_supplier_is_reported_with_its_own_message(self) -> None:
        """A dropped line here is a supplier the owner thinks was handled."""
        _, out = self._run_register(["register"])

        self.assertIn("REGISTRATION SUMMARY", out)
        for result in self.RESULTS:
            with self.subTest(supplier=result["supplier"]):
                self.assertIn(f"{result['supplier']}: {result['status']}", out)
                self.assertIn(result["message"], out)

    def test_output_saves_the_results_and_says_where(self) -> None:
        registrar = MagicMock()
        saved = Path(tempfile.gettempdir()) / "reg-results.json"
        registrar.return_value.save_results.return_value = saved

        with patch("oilwatch.auto_register.AccountRegistrar", registrar):
            _, out = self._run_register(["register", "--output", "reg-results.json"])

        registrar.return_value.save_results.assert_called_once_with(
            "reg-results.json", self.RESULTS
        )
        self.assertIn(f"Results saved to: {saved}", out)

    def test_without_output_nothing_is_saved(self) -> None:
        registrar = MagicMock()
        with patch("oilwatch.auto_register.AccountRegistrar", registrar):
            _, out = self._run_register(["register"])

        registrar.assert_not_called()
        self.assertNotIn("Results saved to", out)


# ── login-email ────────────────────────────────────────────────────────


class EmailLoginReportingTests(unittest.TestCase):
    """The owner reads this line to decide whether the monitor can run."""

    def _run_login_email(self, result: dict) -> str:
        monitor = MagicMock()
        monitor.interactive_login.return_value = result
        with patch("oilwatch.graph_email.GraphEmailMonitor", return_value=monitor):
            _, out = run_cli(["login-email"])
        return out

    def test_a_cached_token_is_reported_as_such(self) -> None:
        out = self._run_login_email({"access_token": "secret-token-value"})

        self.assertIn("Email authentication successful", out)
        # The token is a credential; it must not be echoed to the terminal.
        self.assertNotIn("secret-token-value", out)

    def test_a_failure_reports_the_reason_and_never_claims_success(self) -> None:
        out = self._run_login_email({"error_description": "consent declined by admin"})

        self.assertIn("consent declined by admin", out)
        self.assertNotIn("successful", out)

    def test_a_bare_error_code_is_still_reported(self) -> None:
        out = self._run_login_email({"error": "authorization_pending"})

        self.assertIn("authorization_pending", out)
        self.assertNotIn("successful", out)


if __name__ == "__main__":
    unittest.main()
