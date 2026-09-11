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

    def test_the_benchmark_is_reported_but_not_counted(self) -> None:
        """Fueltool publishes a UK average: context, not a supplier's offer.

        It used to sit in the average and the variance, and would have been
        named the cheapest supplier whenever the UK average dipped below every
        local quote.
        """
        quotes = [
            make_quote(1, "A", "2026-03-20T10:00:00", 0.71),
            make_quote(2, "B", "2026-03-20T11:00:00", 0.74),
            {**make_quote(3, "Fueltool", "2026-03-20T12:00:00", 0.50), "source": "fueltool"},
        ]

        result = AnalyticsService.latest_market_snapshot(quotes)

        self.assertEqual(result["cheapest_supplier"]["name"], "A")  # not the benchmark
        self.assertEqual(result["average_price_per_liter"], 0.725)  # suppliers only
        self.assertEqual(result["quotes_considered"], 2)
        self.assertEqual(result["benchmark"], {"name": "Fueltool", "price_per_liter": 0.5})

    def test_a_benchmark_alone_leaves_no_supplier_to_compare(self) -> None:
        quotes = [
            {**make_quote(3, "Fueltool", "2026-03-20T12:00:00", 0.50), "source": "fueltool"}
        ]

        result = AnalyticsService.latest_market_snapshot(quotes)

        self.assertIsNone(result["cheapest_supplier"])
        self.assertIsNone(result["average_price_per_liter"])
        self.assertEqual(result["quotes_considered"], 0)
        self.assertEqual(result["benchmark"]["name"], "Fueltool")


class TrendTests(unittest.TestCase):
    def test_insufficient_data(self) -> None:
        result = AnalyticsService.price_trend([])
        self.assertEqual(result["direction"], "insufficient_data")
        self.assertIsNone(result["moving_average_7d"])
        self.assertEqual(result["suppliers_compared"], 0)
        self.assertEqual(result["change_by_supplier"], {})

    def test_single_day_is_stable(self) -> None:
        quotes = [make_quote(1, "A", "2026-03-20T10:00:00", 0.71)]
        result = AnalyticsService.price_trend(quotes)
        self.assertEqual(result["direction"], "stable")
        self.assertIsNone(result["latest_change"])
        self.assertEqual(result["moving_average_7d"], 0.71)
        self.assertEqual(result["suppliers_compared"], 0)

    def test_rising(self) -> None:
        quotes = [
            make_quote(1, "A", "2026-03-20T10:00:00", 0.70),
            make_quote(1, "A", "2026-03-21T10:00:00", 0.72),
        ]
        result = AnalyticsService.price_trend(quotes)
        self.assertEqual(result["direction"], "rising")
        self.assertEqual(result["latest_change"], 0.02)
        self.assertEqual(result["moving_average_7d"], 0.71)
        self.assertEqual(result["suppliers_compared"], 1)
        self.assertEqual(result["change_by_supplier"], {"A": 0.02})

    def test_falling(self) -> None:
        quotes = [
            make_quote(1, "A", "2026-03-20T10:00:00", 0.72),
            make_quote(1, "A", "2026-03-21T10:00:00", 0.70),
        ]
        result = AnalyticsService.price_trend(quotes)
        self.assertEqual(result["direction"], "falling")
        self.assertEqual(result["latest_change"], -0.02)

    def test_a_departing_supplier_does_not_manufacture_a_rise(self) -> None:
        """B was the daily cheapest, then stopped quoting.

        The daily minimum rises from 0.70 to 0.71 — yet A, the only supplier to
        quote both days, actually *fell* from 0.75. Judging the market by the
        daily minimum reported a rise the market did not have.
        """
        quotes = [
            make_quote(1, "A", "2026-03-20T10:00:00", 0.75),
            make_quote(2, "B", "2026-03-20T11:00:00", 0.70),
            make_quote(1, "A", "2026-03-21T10:00:00", 0.71),
        ]
        result = AnalyticsService.price_trend(quotes)
        self.assertEqual(result["cheapest_per_day"], [0.70, 0.71])
        self.assertEqual(result["direction"], "falling")
        self.assertEqual(result["change_by_supplier"], {"A": -0.04})
        self.assertEqual(result["suppliers_compared"], 1)

    def test_one_supplier_reprice_does_not_swing_the_market(self) -> None:
        """The headline regression: one mover among three still is not a market move."""
        quotes = []
        for i, (name, price) in enumerate(
            [("A", 0.70), ("B", 0.74), ("C", 0.78), ("D", 0.80)], start=1
        ):
            quotes.append(make_quote(i, name, "2026-03-20T10:00:00", price))
        quotes += [
            make_quote(1, "A", "2026-03-21T10:00:00", 0.75),  # +0.05, and now the cheapest
            make_quote(2, "B", "2026-03-21T10:00:00", 0.74),
            make_quote(3, "C", "2026-03-21T10:00:00", 0.78),
            make_quote(4, "D", "2026-03-21T10:00:00", 0.80),
        ]
        result = AnalyticsService.price_trend(quotes)
        self.assertEqual(result["direction"], "stable")
        self.assertEqual(result["latest_change"], 0.0)
        self.assertEqual(result["suppliers_compared"], 4)

    def test_a_common_penny_move_is_the_signal(self) -> None:
        """When the suppliers move together, that is the market."""
        quotes = []
        for i, (name, before, after) in enumerate(
            [("A", 0.70, 0.71), ("B", 0.74, 0.75), ("C", 0.78, 0.79)], start=1
        ):
            quotes.append(make_quote(i, name, "2026-03-20T10:00:00", before))
            quotes.append(make_quote(i, name, "2026-03-21T10:00:00", after))
        result = AnalyticsService.price_trend(quotes)
        self.assertEqual(result["direction"], "rising")
        self.assertEqual(result["latest_change"], 0.01)

    def test_a_sub_penny_move_is_noise(self) -> None:
        """Below the quote's own resolution, this is rounding, not a movement."""
        quotes = []
        for i, (name, before, after) in enumerate(
            [("A", 0.700, 0.705), ("B", 0.740, 0.745), ("C", 0.780, 0.785)], start=1
        ):
            quotes.append(make_quote(i, name, "2026-03-20T10:00:00", before))
            quotes.append(make_quote(i, name, "2026-03-21T10:00:00", after))
        result = AnalyticsService.price_trend(quotes)
        self.assertEqual(result["direction"], "stable")
        self.assertEqual(result["latest_change"], 0.005)

    def test_the_benchmark_is_not_part_of_the_market_trend(self) -> None:
        """Fueltool's UK average is context, not a participant — here too.

        It is already held out of the snapshot's cheapest/average/variance; the
        trend is the same kind of market statistic, so its UK-average row must
        not count as a supplier that moved, nor set the daily cheapest.
        """
        quotes = [
            make_quote(1, "A", "2026-03-20T10:00:00", 0.70),
            make_quote(1, "A", "2026-03-21T10:00:00", 0.72),
            {**make_quote(9, "Fueltool", "2026-03-20T10:00:00", 0.10), "source": "fueltool"},
            {**make_quote(9, "Fueltool", "2026-03-21T10:00:00", 0.90), "source": "fueltool"},
        ]
        result = AnalyticsService.price_trend(quotes)
        self.assertNotIn("Fueltool", result["change_by_supplier"])
        self.assertEqual(result["suppliers_compared"], 1)
        self.assertEqual(result["cheapest_per_day"], [0.70, 0.72])  # not the UK average
        self.assertEqual(result["direction"], "rising")


class RecommendationTests(unittest.TestCase):
    def test_no_quotes(self) -> None:
        snapshot = AnalyticsService.latest_market_snapshot([])
        trend = AnalyticsService.price_trend([])
        text = AnalyticsService.recommendation(snapshot, trend)
        self.assertIn("No successful quotes", text)

    def test_a_fall_is_reported_with_its_basis(self) -> None:
        quotes = []
        for i, (name, before, after) in enumerate(
            [("A", 0.72, 0.70), ("B", 0.76, 0.74)], start=1
        ):
            quotes.append(make_quote(i, name, "2026-03-20T10:00:00", before))
            quotes.append(make_quote(i, name, "2026-03-21T10:00:00", after))
        snapshot = AnalyticsService.latest_market_snapshot(quotes)
        trend = AnalyticsService.price_trend(quotes)
        text = AnalyticsService.recommendation(snapshot, trend)
        self.assertIn("fell 2.0p/L", text)
        self.assertIn("2 suppliers", text)

    def test_a_rise_is_reported_with_its_basis(self) -> None:
        quotes = []
        for i, (name, before, after) in enumerate(
            [("A", 0.70, 0.72), ("B", 0.74, 0.76)], start=1
        ):
            quotes.append(make_quote(i, name, "2026-03-20T10:00:00", before))
            quotes.append(make_quote(i, name, "2026-03-21T10:00:00", after))
        snapshot = AnalyticsService.latest_market_snapshot(quotes)
        trend = AnalyticsService.price_trend(quotes)
        text = AnalyticsService.recommendation(snapshot, trend)
        self.assertIn("rose 2.0p/L", text)

    def test_a_lone_supplier_move_is_attributed_to_that_supplier(self) -> None:
        quotes = [
            make_quote(1, "A", "2026-03-20T10:00:00", 0.70),
            make_quote(1, "A", "2026-03-21T10:00:00", 0.75),
        ]
        snapshot = AnalyticsService.latest_market_snapshot(quotes)
        trend = AnalyticsService.price_trend(quotes)
        text = AnalyticsService.recommendation(snapshot, trend)
        self.assertIn("1 supplier ", text)  # singular: one supplier is the whole basis
        self.assertNotIn("suppliers", text)

    def test_one_supplier_move_does_not_claim_a_market_direction(self) -> None:
        """The wording half of the regression: three still, one moved."""
        quotes = [
            make_quote(1, "A", "2026-03-20T10:00:00", 0.70),
            make_quote(2, "B", "2026-03-20T10:00:00", 0.74),
            make_quote(3, "C", "2026-03-20T10:00:00", 0.78),
            make_quote(1, "A", "2026-03-21T10:00:00", 0.75),
            make_quote(2, "B", "2026-03-21T10:00:00", 0.74),
            make_quote(3, "C", "2026-03-21T10:00:00", 0.78),
        ]
        snapshot = AnalyticsService.latest_market_snapshot(quotes)
        trend = AnalyticsService.price_trend(quotes)
        self.assertEqual(trend["direction"], "stable")
        text = AnalyticsService.recommendation(snapshot, trend)
        self.assertIn("little changed", text)
        self.assertNotIn("rose", text)
        self.assertNotIn("fell", text)

    def test_no_direction_without_a_comparison(self) -> None:
        quotes = [make_quote(1, "A", "2026-03-20T10:00:00", 0.71)]
        snapshot = AnalyticsService.latest_market_snapshot(quotes)
        trend = AnalyticsService.price_trend(quotes)
        text = AnalyticsService.recommendation(snapshot, trend)
        self.assertIn("Not enough quotes", text)


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
