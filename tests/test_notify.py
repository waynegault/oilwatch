from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from oilwatch import notify


class _Completed:
    def __init__(self, returncode: int = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stderr = stderr


class NotifyTests(unittest.TestCase):
    """A notification is a nicety: it must never raise into a caller's run."""

    def test_builds_a_powershell_toast_without_interpolating_content(self) -> None:
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return _Completed()

        with patch("oilwatch.notify.available", return_value=True):
            shown = notify.notify("Prices failed", "Rix: timeout", runner=runner)

        self.assertTrue(shown)
        command, kwargs = calls[0]
        self.assertEqual(command[0], "powershell")
        self.assertNotIn("Rix: timeout", " ".join(command), "content must not enter the script")
        self.assertEqual(kwargs["env"]["OILWATCH_TOAST_MESSAGE"], "Rix: timeout")

    def test_escapes_xml_metacharacters_in_the_message(self) -> None:
        captured = {}

        def runner(command, **kwargs):
            captured.update(kwargs["env"])
            return _Completed()

        with patch("oilwatch.notify.available", return_value=True):
            notify.notify("Title", "Gleaner & Sons <quotes>", runner=runner)

        self.assertEqual(captured["OILWATCH_TOAST_MESSAGE"], "Gleaner &amp; Sons &lt;quotes&gt;")

    def test_a_failing_runner_returns_false_rather_than_raising(self) -> None:
        def runner(*_args, **_kwargs):
            raise OSError("powershell disappeared")

        with patch("oilwatch.notify.available", return_value=True):
            self.assertFalse(notify.notify("Title", "Body", runner=runner))

    def test_a_non_zero_exit_returns_false(self) -> None:
        with patch("oilwatch.notify.available", return_value=True):
            shown = notify.notify("Title", "Body", runner=lambda *a, **k: _Completed(1, b"nope"))
        self.assertFalse(shown)

    def test_skips_without_a_backend(self) -> None:
        with patch("oilwatch.notify.available", return_value=False):
            self.assertFalse(notify.notify("Title", "Body", runner=lambda *a, **k: _Completed()))


class NotifyErrorsTests(unittest.TestCase):
    def test_no_failures_means_no_toast(self) -> None:
        self.assertFalse(notify.notify_errors([]))

    def test_summarises_and_truncates_the_supplier_list(self) -> None:
        sent = {}

        with patch("oilwatch.notify.notify", side_effect=lambda t, m: sent.update(title=t, message=m) or True):
            notify.notify_errors(["A", "B", "C", "D", "E"])

        self.assertEqual(sent["title"], "OilWatch quote collection")
        self.assertIn("5 supplier(s) failed", sent["message"])
        self.assertIn("(+2 more)", sent["message"])


class ServiceIntegrationTests(unittest.TestCase):
    """quote_all should raise a toast exactly when a connector errored."""

    def _app(self):
        from oilwatch.service import OilWatchApp

        # Only the members quote_all touches are needed.
        app = SimpleNamespace(
            db=SimpleNamespace(
                init_schema=lambda: None,
                list_suppliers=lambda include_inactive=False: [
                    {"id": 1, "name": "Good", "connector_type": "manual", "connector_config": {}},
                    {"id": 2, "name": "Bad", "connector_type": "manual", "connector_config": {}},
                ],
                record_quote=lambda payload: None,
            ),
            settings=SimpleNamespace(quote_quantity_liters=1000, currency="GBP"),
        )
        app.quotes = SimpleNamespace(quote_supplier=self._quote)
        app.quote_all = lambda **kwargs: OilWatchApp.quote_all(app, **kwargs)
        return app

    @staticmethod
    def _quote(supplier, quantity, postcode=None, prefer_browser=False):
        from oilwatch.models import QuoteResult

        if supplier["name"] == "Bad":
            raise RuntimeError("connector exploded")
        return QuoteResult(
            supplier_id=int(supplier["id"]),
            supplier_name=supplier["name"],
            observed_at=__import__("datetime").datetime(2026, 9, 10, 12, 0, 0),
            quantity_liters=quantity,
            status="ok",
            price_per_liter=1.0,
            total_price=1000.0,
        )

    def test_toast_is_raised_only_for_the_failing_supplier(self) -> None:
        app = self._app()
        with patch("oilwatch.notify.notify", return_value=True) as toast, self.assertLogs(
            "oilwatch.service", level="WARNING"
        ):
            results = app.quote_all()

        self.assertEqual([r["status"] for r in results], ["ok", "error"])
        self.assertEqual(toast.call_count, 1)
        self.assertIn("Bad", toast.call_args[0][1])

    def test_no_toast_when_everything_succeeds(self) -> None:
        app = self._app()
        app.db.list_suppliers = lambda include_inactive=False: [
            {"id": 1, "name": "Good", "connector_type": "manual", "connector_config": {}}
        ]
        with patch("oilwatch.notify.notify", return_value=True) as toast:
            app.quote_all()
        self.assertEqual(toast.call_count, 0)


if __name__ == "__main__":
    unittest.main()
