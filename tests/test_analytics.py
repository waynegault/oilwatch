from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from oilwatch.analytics import AnalyticsService


def make_quote(
    supplier_id: int,
    name: str,
    observed_at: str,
    price_per_liter: float,
    status: str = "ok",
) -> dict:
    return {
        "supplier_id": supplier_id,
        "supplier_name": name,
        "website": f"https://{name.lower().replace(' ', '')}.example.com",
        "observed_at": observed_at,
        "status": status,
        "price_per_liter": price_per_liter,
    }


class SnapshotTests(unittest.TestCase):
    def test_empty(self) -> None:
        result = AnalyticsService.latest_market_snapshot([])
        self.assertIsNone(result["cheapest_supplier"])
        self.assertIsNone(result["average_price_per_liter"])
        self.assertEqual(result["quotes_considered"], 0)

    def test_cheapest_and_average(self) -> None:
        quotes = [
            make_quote(1, "A", "2026-03-20T10:00:00", 0.71),
            make_quote(2, "B", "2026-03-20T11:00:00", 0.74),
        ]
        result = AnalyticsService.latest_market_snapshot(quotes)
        self.assertEqual(result["cheapest_supplier"]["name"], "A")
        self.assertEqual(result["cheapest_supplier"]["price_per_liter"], 0.71)
        self.assertEqual(result["average_price_per_liter"], 0.725)
        self.assertEqual(result["quotes_considered"], 2)


class TrendTests(unittest.TestCase):
    def test_insufficient_data(self) -> None:
        result = AnalyticsService.price_trend([])
        self.assertEqual(result["direction"], "insufficient_data")
        self.assertIsNone(result["moving_average_7d"])

    def test_single_day_is_stable(self) -> None:
        quotes = [make_quote(1, "A", "2026-03-20T10:00:00", 0.71)]
        result = AnalyticsService.price_trend(quotes)
        self.assertEqual(result["direction"], "stable")
        self.assertIsNone(result["latest_change"])
        self.assertEqual(result["moving_average_7d"], 0.71)

    def test_rising(self) -> None:
        quotes = [
            make_quote(1, "A", "2026-03-20T10:00:00", 0.70),
            make_quote(1, "A", "2026-03-21T10:00:00", 0.72),
        ]
        result = AnalyticsService.price_trend(quotes)
        self.assertEqual(result["direction"], "rising")
        self.assertEqual(result["latest_change"], 0.02)
        self.assertEqual(result["moving_average_7d"], 0.71)

    def test_falling(self) -> None:
        quotes = [
            make_quote(1, "A", "2026-03-20T10:00:00", 0.72),
            make_quote(1, "A", "2026-03-21T10:00:00", 0.70),
        ]
        result = AnalyticsService.price_trend(quotes)
        self.assertEqual(result["direction"], "falling")
        self.assertEqual(result["latest_change"], -0.02)

    def test_uses_daily_cheapest(self) -> None:
        # Two suppliers on the same day: only the cheapest drives the trend.
        quotes = [
            make_quote(1, "A", "2026-03-20T10:00:00", 0.75),
            make_quote(2, "B", "2026-03-20T11:00:00", 0.70),
            make_quote(1, "A", "2026-03-21T10:00:00", 0.71),
        ]
        result = AnalyticsService.price_trend(quotes)
        self.assertEqual(result["cheapest_per_day"], [0.70, 0.71])
        self.assertEqual(result["direction"], "rising")


class RecommendationTests(unittest.TestCase):
    def test_no_quotes(self) -> None:
        snapshot = AnalyticsService.latest_market_snapshot([])
        trend = AnalyticsService.price_trend([])
        text = AnalyticsService.recommendation(snapshot, trend)
        self.assertIn("No successful quotes", text)

    def test_falling_recommends_waiting(self) -> None:
        quotes = [
            make_quote(1, "A", "2026-03-20T10:00:00", 0.72),
            make_quote(1, "A", "2026-03-21T10:00:00", 0.70),
        ]
        snapshot = AnalyticsService.latest_market_snapshot(quotes)
        trend = AnalyticsService.price_trend(quotes)
        text = AnalyticsService.recommendation(snapshot, trend)
        self.assertIn("falling", text)
        self.assertIn("A", text)

    def test_rising_recommends_buying(self) -> None:
        quotes = [
            make_quote(1, "A", "2026-03-20T10:00:00", 0.70),
            make_quote(1, "A", "2026-03-21T10:00:00", 0.72),
        ]
        snapshot = AnalyticsService.latest_market_snapshot(quotes)
        trend = AnalyticsService.price_trend(quotes)
        text = AnalyticsService.recommendation(snapshot, trend)
        self.assertIn("rising", text)
        self.assertIn("A", text)


class TimeSeriesChartTests(unittest.TestCase):
    def test_empty_raises(self) -> None:
        with self.assertRaises(ValueError):
            AnalyticsService.build_time_series_chart([], Path("unused.png"))

    def test_chart_writes_file_with_multiple_suppliers(self) -> None:
        quotes = [
            make_quote(1, "A", "2026-03-20T10:00:00", 0.70),
            make_quote(1, "A", "2026-03-21T10:00:00", 0.72),
            make_quote(2, "B", "2026-03-20T10:00:00", 0.75),
            make_quote(2, "B", "2026-03-21T10:00:00", 0.73),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "time-series.png"
            result = AnalyticsService.build_time_series_chart(quotes, out)
            self.assertEqual(result, out)
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 0)

    def test_ignores_non_ok_and_malformed_dates(self) -> None:
        quotes = [
            make_quote(1, "A", "2026-03-20T10:00:00", 0.70),
            make_quote(2, "B", "2026-03-20T10:00:00", 0.75, status="error"),
            make_quote(3, "C", "not-a-date", 0.80),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "time-series.png"
            AnalyticsService.build_time_series_chart(quotes, out)
            self.assertTrue(out.exists())

    def test_chart_includes_brent_on_secondary_axis(self) -> None:
        quotes = [make_quote(1, "A", "2026-03-20T10:00:00", 0.70)]
        brent = [
            {"observed_at": "2026-03-20", "price_usd_per_barrel": 70.5},
            {"observed_at": "2026-03-21", "price_usd_per_barrel": 71.2},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "time-series.png"
            AnalyticsService.build_time_series_chart(quotes, out, brent=brent)
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 0)

    def test_chart_requires_quotes_or_brent(self) -> None:
        with self.assertRaises(ValueError):
            AnalyticsService.build_time_series_chart([], Path("unused.png"), brent=[])


class MixedMissingPriceTests(unittest.TestCase):
    """The snapshot must survive a priced row sitting beside an unpriced one.

    ``latest_market_snapshot`` guarded the empty and all-missing cases, but built
    its ``min()`` over the *unfiltered* rows — so one ``ok`` row without a price
    made the comparison raise TypeError instead of reporting a market.
    """

    def test_mixed_missing_prices_picks_the_priced_row(self) -> None:
        quotes = [
            make_quote(1, "NoPrice", "2026-03-20T10:00:00", None),
            make_quote(2, "Priced", "2026-03-20T11:00:00", 0.74),
        ]
        result = AnalyticsService.latest_market_snapshot(quotes)
        self.assertEqual(result["cheapest_supplier"]["name"], "Priced")
        self.assertEqual(result["average_price_per_liter"], 0.74)
        self.assertEqual(result["quotes_considered"], 1)


if __name__ == "__main__":
    unittest.main()
