from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from apscheduler.schedulers.background import BackgroundScheduler

from oilwatch.scheduler import OilWatchScheduler, _window_hours
from oilwatch.service import OilWatchApp


class _StubApp:
    """Stand-in for OilWatchApp: the scheduler only touches these members."""

    def __init__(self, *, fail: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.calls: list[str] = []
        self.postcodes: list[str | None] = []
        self.browser_flags: list[bool] = []
        self.started_by: list[str] = []
        self.init_called = False
        self.settings = SimpleNamespace(
            scheduler=SimpleNamespace(
                discovery_interval_hours=168,
                quote_interval_hours=24,
                email_monitor_interval_hours=1,
                email_monitor_start_hour=8,
                email_monitor_end_hour=18,
                email_monitor_days="mon-fri",
            )
        )

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail:
            raise RuntimeError(f"{name} exploded")

    def quote_all(self, postcode=None, prefer_browser=False, started_by="cli"):
        self._record("quote_all")
        self.postcodes.append(postcode)
        self.browser_flags.append(prefer_browser)
        self.started_by.append(started_by)
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


def _wired(
    *, postcode: str | None = "AB21 0YA", **kwargs: Any
) -> tuple[OilWatchScheduler, _StubApp, _StubScheduler]:
    """A scheduler over a stub app, with apscheduler's scheduler stubbed too.

    Both casts live here and nowhere else: ``_StubApp`` stands in for
    ``OilWatchApp`` and ``_StubScheduler`` for the real scheduler. A protocol for
    the app was the alternative, and it was the worse one — the app is this
    package's own class, and typing the scheduler against an interface of our own
    would stop checking it against the app it actually drives.
    """
    app = _StubApp(**kwargs)
    scheduler = OilWatchScheduler(cast(OilWatchApp, app), postcode=postcode)
    stub = _StubScheduler()
    scheduler.scheduler = cast(BackgroundScheduler, stub)
    return scheduler, app, stub


class RefreshChainTests(unittest.TestCase):
    """The daily refresh chain: critical step first, the rest best-effort."""

    def _scheduler(self, *, postcode: str | None = "AB21 0YA", **kwargs):
        scheduler, app, _ = _wired(postcode=postcode, **kwargs)
        return scheduler, app

    def test_every_step_runs_when_healthy(self) -> None:
        scheduler, app = self._scheduler()
        scheduler._refresh_quotes_and_charts()
        self.assertEqual(
            app.calls, ["quote_all", "update_brent", "chart", "time_series_chart"]
        )
        # And the sweep says who started it, so the marker another caller reads
        # distinguishes a scheduled run from the owner's own.
        self.assertEqual(app.started_by, ["scheduler"])

    def test_postcode_is_passed_through_to_quote_collection(self) -> None:
        scheduler, app = self._scheduler(postcode="ZZ99 9ZZ")
        scheduler._refresh_quotes_and_charts()
        self.assertEqual(app.postcodes, ["ZZ99 9ZZ"])

    def test_the_refresh_asks_for_browser_quotes(self) -> None:
        """The scheduled refresh must cover the browser suppliers too.

        quote_all defaults to prefer_browser=False, so a plain call refreshed the
        HTTP suppliers and left Rix, the Fuelsoft trio and Scottish Fuels
        unrefreshed - they then drop out of the comparison within a day.
        """
        scheduler, app = self._scheduler()
        scheduler._refresh_quotes_and_charts()
        self.assertEqual(app.browser_flags, [True])

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
        scheduler, app, stub = _wired()

        scheduler.start()

        self.assertTrue(app.init_called)
        self.assertTrue(stub.started)
        by_id = {job["id"]: job for job in stub.jobs}
        self.assertEqual(
            sorted(by_id),
            ["discover_suppliers", "monitor_email", "quote_all"],
        )
        # These two stay open-ended intervals. The email sweep's trigger is a
        # window, so it is asserted on its fire times in EmailWindowTests.
        self.assertEqual(by_id["discover_suppliers"]["hours"], 168)
        self.assertEqual(by_id["quote_all"]["hours"], 24)


class RunForeverTests(unittest.TestCase):
    def test_run_forever_starts_the_jobs_and_stops_on_interrupt(self) -> None:
        """The loop only ends on Ctrl-C, and the scheduler has to come down with it."""
        scheduler, _, stub = _wired()

        with patch("oilwatch.scheduler.time.sleep", side_effect=KeyboardInterrupt):
            scheduler.run_forever()  # must not raise

        self.assertEqual(
            sorted(job["id"] for job in stub.jobs),
            ["discover_suppliers", "monitor_email", "quote_all"],
        )
        self.assertFalse(stub.started, "the scheduler should be shut down on the way out")


class WindowHoursTests(unittest.TestCase):
    """The cron hour list a windowed job is built from."""

    def test_the_configured_interval_sets_the_hop(self) -> None:
        self.assertEqual(_window_hours(8, 18, 1), "8,9,10,11,12,13,14,15,16,17,18")
        self.assertEqual(_window_hours(8, 18, 2), "8,10,12,14,16,18")

    def test_an_interval_longer_than_the_window_still_fires_once(self) -> None:
        """The un-configured default is 24h: one sweep a day, at the window's start."""
        self.assertEqual(_window_hours(8, 18, 24), "8")


class EmailWindowTests(unittest.TestCase):
    """The email sweep is confined to the hours suppliers are open.

    Added 2026-09-13: the job was an open hourly interval, so it swept at :39
    past every hour of every day - all weekend, into an inbox no supplier was
    writing to. An interval trigger cannot express a window, so the job is now a
    cron trigger built from the configured hours; these tests assert the fire
    times, not the trigger's internals.
    """

    def _trigger(self):
        scheduler, _, stub = _wired()
        scheduler.start()
        job = next(job for job in stub.jobs if job["id"] == "monitor_email")
        return job["trigger"]

    def test_no_sweep_at_the_weekend(self) -> None:
        trigger = self._trigger()
        friday_evening = datetime(2026, 9, 11, 18, 30, tzinfo=trigger.timezone)
        fire = trigger.get_next_fire_time(None, friday_evening)
        self.assertEqual(fire.weekday(), 0, "Friday evening waits for Monday")
        self.assertEqual((fire.hour, fire.minute), (8, 0))

    def test_sweeps_hourly_across_the_working_day(self) -> None:
        trigger = self._trigger()
        now = datetime(2026, 9, 14, 7, 0, tzinfo=trigger.timezone)  # Monday
        fires = []
        for _ in range(11):
            fire = trigger.get_next_fire_time(None, now)
            fires.append(fire)
            now = fire + timedelta(minutes=1)
        self.assertEqual([fire.hour for fire in fires], list(range(8, 19)))
        self.assertTrue(all(fire.weekday() == 0 for fire in fires))

    def test_the_working_day_does_not_run_overnight(self) -> None:
        """Monday's last sweep is 18:00; the next is Tuesday 08:00, not 03:00.

        A trigger filtered by day alone - the obvious half-fix - fires all night.
        """
        trigger = self._trigger()
        monday_evening = datetime(2026, 9, 14, 18, 1, tzinfo=trigger.timezone)
        fire = trigger.get_next_fire_time(None, monday_evening)
        self.assertEqual((fire.weekday(), fire.hour), (1, 8))


if __name__ == "__main__":
    unittest.main()
