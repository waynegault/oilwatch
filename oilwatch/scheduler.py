from __future__ import annotations

import time

from apscheduler.schedulers.background import BackgroundScheduler

from oilwatch.service import OilWatchApp


class OilWatchScheduler:
    def __init__(self, app: OilWatchApp, postcode: str | None = None) -> None:
        self.app = app
        self.postcode = postcode
        self.scheduler = BackgroundScheduler()

    def _refresh_quotes_and_charts(self) -> None:
        self.app.quote_all(postcode=self.postcode)
        # Non-critical refresh steps: a network/charting failure here must not
        # stop the scheduler, and the next run retries them anyway.
        try:
            self.app.update_brent()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.app.chart()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.app.time_series_chart()
        except Exception:  # noqa: BLE001
            pass

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
        self.scheduler.add_job(
            self.app.monitor_email,
            "interval",
            hours=self.app.settings.scheduler.email_monitor_interval_hours,
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

