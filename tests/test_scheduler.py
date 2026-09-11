from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from oilwatch.scheduler import OilWatchScheduler


class _StubApp:
    """Stand-in for OilWatchApp: the scheduler only touches these members."""

    def __init__(self, *, fail: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.calls: list[str] = []
        self.postcodes: list[str | None] = []
        self.init_called = False
        self.settings = SimpleNamespace(
            scheduler=SimpleNamespace(
                discovery_interval_hours=168,
                quote_interval_hours=24,
                email_monitor_interval_hours=12,
            )
        )

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail:
            raise RuntimeError(f"{name} exploded")

    def quote_all(self, postcode=None):
        self._record("quote_all")
        self.postcodes.append(postcode)
        return []

    def update_brent(self):
        self._record("update_brent")
        return {}

    def chart(self):
        self._record("chart")
        return "chart.png"

    def time_series_chart(self):
        self._record("time_series_chart")
        return "series.png"

    def init(self):
        self.init_called = True
        return {}

    def discover_suppliers(self):
        return []

    def monitor_email(self):
        return {}


class _StubScheduler:
    """Records job registration without starting APScheduler threads."""

    def __init__(self) -> None:
        self.jobs: list[dict] = []
        self.started = False

    def add_job(self, func, trigger, **kwargs) -> None:
        self.jobs.append({"func": func, "trigger": trigger, **kwargs})

    def start(self) -> None:
        self.started = True

    def shutdown(self, wait: bool = False) -> None:
        self.started = False


class RefreshChainTests(unittest.TestCase):
    """The daily refresh chain: critical step first, the rest best-effort."""

    def _scheduler(self, *, postcode: str | None = "AB21 0YA", **kwargs):
        app = _StubApp(**kwargs)
        return OilWatchScheduler(app, postcode=postcode), app

    def test_every_step_runs_when_healthy(self) -> None:
        scheduler, app = self._scheduler()
        scheduler._refresh_quotes_and_charts()
        self.assertEqual(
            app.calls, ["quote_all", "update_brent", "chart", "time_series_chart"]
        )

    def test_postcode_is_passed_through_to_quote_collection(self) -> None:
        scheduler, app = self._scheduler(postcode="ZZ99 9ZZ")
        scheduler._refresh_quotes_and_charts()
        self.assertEqual(app.postcodes, ["ZZ99 9ZZ"])

    def test_non_critical_failures_are_logged_and_do_not_stop_the_chain(self) -> None:
        scheduler, app = self._scheduler(fail={"update_brent", "chart", "time_series_chart"})

        with self.assertLogs("oilwatch.scheduler", level="WARNING") as captured:
            scheduler._refresh_quotes_and_charts()  # must not raise

        self.assertEqual(
            app.calls,
            ["quote_all", "update_brent", "chart", "time_series_chart"],
            "one failing step must not skip the later ones",
        )
        messages = "\n".join(captured.output)
        for expected in ("Brent price update", "Market chart", "Time-series chart"):
            self.assertIn(expected, messages)

    def test_only_the_failed_steps_are_reported(self) -> None:
        scheduler, _ = self._scheduler(fail={"chart"})

        with self.assertLogs("oilwatch.scheduler", level="WARNING") as captured:
            scheduler._refresh_quotes_and_charts()

        self.assertEqual(len(captured.output), 1)
        self.assertIn("Market chart", captured.output[0])

    def test_quote_collection_failure_propagates(self) -> None:
        """quote_all is the critical step — it is deliberately not swallowed."""
        scheduler, _ = self._scheduler(fail={"quote_all"})
        with self.assertRaises(RuntimeError):
            scheduler._refresh_quotes_and_charts()


class StartTests(unittest.TestCase):
    def test_start_registers_three_jobs_and_starts_the_scheduler(self) -> None:
        app = _StubApp()
        scheduler = OilWatchScheduler(app)
        stub = _StubScheduler()
        scheduler.scheduler = stub

        scheduler.start()

        self.assertTrue(app.init_called)
        self.assertTrue(stub.started)
        self.assertEqual(
            sorted(job["id"] for job in stub.jobs),
            ["discover_suppliers", "monitor_email", "quote_all"],
        )
        intervals = {job["id"]: job["hours"] for job in stub.jobs}
        self.assertEqual(intervals["discover_suppliers"], 168)
        self.assertEqual(intervals["quote_all"], 24)
        self.assertEqual(intervals["monitor_email"], 12)


class RunForeverTests(unittest.TestCase):
    def test_run_forever_starts_the_jobs_and_stops_on_interrupt(self) -> None:
        """The loop only ends on Ctrl-C, and the scheduler has to come down with it."""
        app = _StubApp()
        scheduler = OilWatchScheduler(app)
        stub = _StubScheduler()
        scheduler.scheduler = stub

        with patch("oilwatch.scheduler.time.sleep", side_effect=KeyboardInterrupt):
            scheduler.run_forever()  # must not raise

        self.assertEqual(
            sorted(job["id"] for job in stub.jobs),
            ["discover_suppliers", "monitor_email", "quote_all"],
        )
        self.assertFalse(stub.started, "the scheduler should be shut down on the way out")


if __name__ == "__main__":
    unittest.main()
