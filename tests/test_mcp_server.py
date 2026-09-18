"""The MCP server's declared identity, lazy wiring, default postcode and transport.

This file was deleted once as five delegations and a getter, and this is not
that. What is pinned here is the wiring that had drifted: a served version the
handshake took from the MCP framework because the server never declared one, a
delivery postcode repeated as a literal here while every other caller read it
from configuration, that importing the module owns no database, and that
starting it imports no charting stack.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from oilwatch import __version__, mcp_server
from oilwatch.identity import Contact

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Generous: the child only imports the package (1-4s normally). A bound is here
#: so a stalled child fails this test loudly instead of hanging the suite.
SUBPROCESS_TIMEOUT_S = 60


class DeclaredIdentityTests(unittest.TestCase):
    def test_the_server_declares_the_package_version(self) -> None:
        """The handshake reports this field; it reported FastMCP's own instead."""
        self.assertEqual(mcp_server.mcp._mcp_server.version, __version__)


class LazyAppTests(unittest.TestCase):
    def test_the_app_is_built_once_on_first_use(self) -> None:
        """Importing the module must not build the app; first use does, once."""
        with (
            patch("oilwatch.mcp_server._app", None),
            patch("oilwatch.mcp_server.OilWatchApp") as app_cls,
        ):
            first = mcp_server._get_app()
            second = mcp_server._get_app()

        app_cls.assert_called_once_with(mcp_server.ROOT)
        self.assertIs(first, second)


class ImportCostTests(unittest.TestCase):
    """Starting the server must not pay for a chart nobody asked for.

    An MCP client spawns this module as a child and measures its initialize
    timeout from the spawn, so every import on this path is spent against that
    budget. matplotlib used to be one of them (via oilwatch.service ->
    oilwatch.analytics) and cost most of a second of it. Checked in a subprocess
    so a matplotlib import elsewhere in the suite cannot mask a regression.
    """

    def _import_and_report(self, module: str) -> subprocess.CompletedProcess:
        code = (
            "import sys; "
            f"import {module}; "
            "raise SystemExit(1 if any(m.split('.')[0] == 'matplotlib' for m in sys.modules) else 0)"
        )
        try:
            return subprocess.run(
                [sys.executable, "-c", code],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_S,
                # The caller asserts on the return code, so a non-zero exit is
                # the result under test rather than an error to raise here.
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.fail(
                f"importing {module} did not finish within {SUBPROCESS_TIMEOUT_S}s; "
                f"it may be blocked, not slow"
            )

    def test_importing_the_server_does_not_import_matplotlib(self) -> None:
        proc = self._import_and_report("oilwatch.mcp_server")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_importing_analytics_does_not_import_matplotlib(self) -> None:
        proc = self._import_and_report("oilwatch.analytics")
        self.assertEqual(proc.returncode, 0, proc.stderr)


class DefaultPostcodeTests(unittest.TestCase):
    def _app(self) -> MagicMock:
        app = MagicMock()
        app.quote_all.return_value = []
        # No sweep on record, so the cooldown lets the scrape through: a bare
        # MagicMock answers every question truthily, which would trip it.
        app.refresh_recently_done.return_value = None
        return app

    def test_refresh_prices_defaults_to_the_configured_postcode(self) -> None:
        app = self._app()
        with (
            patch("oilwatch.mcp_server.load_contact", return_value=Contact(postcode="ZZ9 9ZZ")),
            patch("oilwatch.mcp_server._get_app", return_value=app),
        ):
            result = mcp_server.refresh_prices()

        self.assertEqual(app.quote_all.call_args.kwargs["postcode"], "ZZ9 9ZZ")
        self.assertFalse(result["cached"])
        self.assertEqual(result["results"], [])

    def test_an_explicit_postcode_is_used_as_given(self) -> None:
        app = self._app()
        with (
            patch("oilwatch.mcp_server.load_contact", return_value=Contact(postcode="ZZ9 9ZZ")),
            patch("oilwatch.mcp_server._get_app", return_value=app),
        ):
            mcp_server.refresh_prices("AB1 1AA")

        self.assertEqual(app.quote_all.call_args.kwargs["postcode"], "AB1 1AA")

    def test_the_fresh_envelope_dates_itself_from_the_rows(self) -> None:
        """refreshed_at is this sweep's own timestamp, not a second query."""
        app = self._app()
        app.quote_all.return_value = [
            {"supplier_name": "A", "status": "ok", "observed_at": "2026-09-18T09:00:00"},
            {"supplier_name": "B", "status": "error", "observed_at": "2026-09-18T09:04:00"},
        ]
        with (
            patch("oilwatch.mcp_server.load_contact", return_value=Contact(postcode="ZZ9 9ZZ")),
            patch("oilwatch.mcp_server._get_app", return_value=app),
        ):
            result = mcp_server.refresh_prices()

        self.assertFalse(result["cached"])
        self.assertEqual(result["refreshed_at"], "2026-09-18T09:04:00")
        self.assertEqual(len(result["results"]), 2)


class RefreshCooldownTests(unittest.TestCase):
    """A sweep is minutes of real browsers, so a retry must not start another.

    The likeliest cause of a second call is a client timeout mid-sweep — the
    default per-call budget is 60 s against a 1-3 minute sweep — so the tool
    refuses and points at the prices already on record instead.
    """

    def _app(self, recent: dict | None) -> MagicMock:
        app = MagicMock()
        app.quote_all.return_value = []
        app.refresh_recently_done.return_value = recent
        return app

    def test_a_recent_sweep_is_not_repeated(self) -> None:
        app = self._app({"refreshed_at": "2026-09-18T09:00:00", "minutes_ago": 2.5})
        with (
            patch("oilwatch.mcp_server.load_contact", return_value=Contact(postcode="ZZ9 9ZZ")),
            patch("oilwatch.mcp_server._get_app", return_value=app),
        ):
            result = mcp_server.refresh_prices()

        app.quote_all.assert_not_called()
        self.assertTrue(result["cached"])
        self.assertEqual(result["results"], [])
        self.assertEqual(result["refreshed_at"], "2026-09-18T09:00:00")
        self.assertEqual(result["minutes_ago"], 2.5)
        self.assertIn("current_prices", result["note"])

    def test_force_sweeps_anyway(self) -> None:
        app = self._app({"refreshed_at": "2026-09-18T09:00:00", "minutes_ago": 2.5})
        with (
            patch("oilwatch.mcp_server.load_contact", return_value=Contact(postcode="ZZ9 9ZZ")),
            patch("oilwatch.mcp_server._get_app", return_value=app),
        ):
            result = mcp_server.refresh_prices(force=True)

        self.assertFalse(result["cached"])
        self.assertTrue(app.quote_all.called)
        # Skipped entirely rather than consulted and overruled.
        app.refresh_recently_done.assert_not_called()


class TransportSelectionTests(unittest.TestCase):
    """``--stdio`` serves the same tools without opening a socket.

    That is how an MCP client can spawn the server on demand instead of
    requiring a running service, so which transport ``main()`` picks is worth
    pinning: the default must stay HTTP, and ``--stdio`` must open no host/port.
    """

    def _run_main(self, argv: list[str]) -> MagicMock:
        run = MagicMock()
        with (
            patch("oilwatch.mcp_server.configure_logging"),
            patch("oilwatch.mcp_server.mcp.run", run),
            patch("oilwatch.mcp_server.sys.argv", ["oilwatch-mcp", *argv]),
        ):
            mcp_server.main()
        return run

    def test_without_arguments_it_serves_http_on_the_configured_host_and_port(self) -> None:
        run = self._run_main([])
        run.assert_called_once_with(transport="http", host=mcp_server.HOST, port=mcp_server.PORT)

    def test_stdio_serves_over_stdio_and_opens_no_socket(self) -> None:
        run = self._run_main(["--stdio"])
        run.assert_called_once_with(transport="stdio")


if __name__ == "__main__":
    unittest.main()
