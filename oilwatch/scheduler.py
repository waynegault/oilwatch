from __future__ import annotations

import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from oilwatch.logging_setup import get_logger
from oilwatch.service import OilWatchApp

log = get_logger("scheduler")


def _window_hours(start: int, end: int, step: int) -> str:
    """Cron hour list for a daily window, e.g. ``(8, 18, 2)`` -> ``8,10,...,18``."""
    return ",".join(str(hour) for hour in range(start, end + 1, step))


class OilWatchScheduler:
    def __init__(self, app: OilWatchApp, postcode: str | None = None) -> None:
        self.app = app
        self.postcode = postcode
        self.scheduler = BackgroundScheduler()

    def _refresh_quotes_and_charts(self) -> None:
        self.app.quote_all(postcode=self.postcode, prefer_browser=True, started_by="scheduler")
        # Non-critical refresh steps: a network/charting failure here must not
        # stop the scheduler, and the next run retries them anyway.
        try:
            self.app.update_brent()
        except Exception as exc:  # noqa: BLE001 - next run retries it
            log.warning("Brent price update failed: %s", exc)
        try:
            self.app.chart()
        except Exception as exc:  # noqa: BLE001 - charting is cosmetic
            log.warning("Market chart failed: %s", exc)
        try:
            self.app.time_series_chart()
        except Exception as exc:  # noqa: BLE001 - charting is cosmetic
            log.warning("Time-series chart failed: %s", exc)

    def start(self) -> None:
        self.app.init()
        self.scheduler.add_job(
            self.app.discover_suppliers,
            "interval",
            hours=self.app.settings.scheduler.discovery_interval_hours,
            id="discover_suppliers",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self._refresh_quotes_and_charts,
            "interval",
            hours=self.app.settings.scheduler.quote_interval_hours,
            id="quote_all",
            replace_existing=True,
        )
        # Replies only arrive while suppliers are open, so the sweep is a weekday
        # business-hours cron rather than an open interval: the interval it
        # replaced fired at :39 past every hour of every day, weekends included.
        cfg = self.app.settings.scheduler
        self.scheduler.add_job(
            self.app.monitor_email,
            CronTrigger(
                day_of_week=cfg.email_monitor_days,
                hour=_window_hours(
                    cfg.email_monitor_start_hour,
                    cfg.email_monitor_end_hour,
                    cfg.email_monitor_interval_hours,
                ),
                minute=0,
            ),
            id="monitor_email",
            replace_existing=True,
        )
        self.scheduler.start()

    def run_forever(self) -> None:
        self.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.scheduler.shutdown(wait=False)

