from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median, pvariance
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


#: Sources whose price is market context rather than an offer from a supplier.
#: Fueltool publishes a UK average, so it must not win "cheapest" and must not
#: drag the average or the variance around; it is reported separately instead.
BENCHMARK_SOURCES = frozenset({"fueltool"})

#: The smallest market move worth reporting. Supplier quotes resolve to a penny
#: per litre, so anything below this is rounding, not a price movement.
TREND_THRESHOLD = 0.01


class AnalyticsService:
    @staticmethod
    def latest_market_snapshot(
        latest_quotes: list[dict[str, Any]],
        excluded_suppliers: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """The market as the owner would compare it: suppliers, plus the benchmark.

        Prices are the effective ones (after any usable discount code), so the
        ranking, the average and the variance all describe the same thing — what
        an order would actually cost.

        ``excluded_suppliers`` names the suppliers a recency window dropped, so
        a thin market reads as "not re-quoted yet" rather than looking empty.
        """
        priced = [row for row in latest_quotes if row["price_per_liter"] is not None]

        # A comparison site publishing a UK average is not a supplier asking for
        # the order, so it is held apart from the market it describes.
        offers = [row for row in priced if row.get("source") not in BENCHMARK_SOURCES]
        benchmark = None
        for row in priced:
            if row.get("source") in BENCHMARK_SOURCES:
                benchmark = {
                    "name": row["supplier_name"],
                    "price_per_liter": row["price_per_liter"],
                }
                break

        if not offers:
            return {
                "cheapest_supplier": None,
                "average_price_per_liter": None,
                "variance": None,
                "quotes_considered": 0,
                "benchmark": benchmark,
                "excluded_suppliers": list(excluded_suppliers or []),
            }

        def effective_of(row: dict[str, Any]) -> float:
            """What the order would cost: the posted price less any useful code."""
            effective = row.get("effective_price_per_liter")
            return float(effective if effective is not None else row["price_per_liter"])

        prices = [effective_of(row) for row in offers]
        cheapest = min(offers, key=effective_of)
        return {
            "cheapest_supplier": {
                "supplier_id": cheapest["supplier_id"],
                "name": cheapest["supplier_name"],
                "website": cheapest["website"],
                "price_per_liter": cheapest["price_per_liter"],
                "observed_at": cheapest["observed_at"],
                # Surfaced so a comparison says how long the offer stands, not
                # just what it costs.
                "valid_until": cheapest.get("valid_until"),
                # And what it costs once any discount code is applied: the stored
                # price already includes 5% VAT.
                "effective_price_per_liter": cheapest.get(
                    "effective_price_per_liter", cheapest["price_per_liter"]
                ),
                "discount": cheapest.get("discount"),
            },
            "average_price_per_liter": round(mean(prices), 4),
            "variance": round(pvariance(prices), 6) if len(prices) > 1 else 0.0,
            "quotes_considered": len(prices),
            # The excluded figure, so it is visible rather than silently dropped.
            "benchmark": benchmark,
            # And the suppliers the recency window held back, by name.
            "excluded_suppliers": list(excluded_suppliers or []),
        }

    @staticmethod
    def _ok_priced(quotes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Successful quotes carrying a price — the only ones that compare.

        Every market/statistic view filters on exactly this, so it lives in one
        place rather than being restated in each grouping loop below.
        """
        return [
            quote
            for quote in quotes
            if quote["status"] == "ok" and quote["price_per_liter"] is not None
        ]

    @staticmethod
    def _day_of(quote: dict[str, Any]) -> str:
        """The ``YYYY-MM-DD`` a quote belongs to."""
        return datetime.fromisoformat(quote["observed_at"]).date().isoformat()

    @staticmethod
    def _daily_cheapest(quotes: list[dict[str, Any]]) -> dict[str, float]:
        """Map ``YYYY-MM-DD`` -> cheapest successful price that day."""
        grouped: dict[str, list[float]] = defaultdict(list)
        for quote in AnalyticsService._ok_priced(quotes):
            grouped[AnalyticsService._day_of(quote)].append(float(quote["price_per_liter"]))
        return {day: min(prices) for day, prices in grouped.items()}

    @staticmethod
    def _daily_by_supplier(quotes: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
        """Map supplier name -> ``YYYY-MM-DD`` -> cheapest price that supplier gave."""
        grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for quote in AnalyticsService._ok_priced(quotes):
            grouped[quote["supplier_name"]][AnalyticsService._day_of(quote)].append(
                float(quote["price_per_liter"])
            )
        return {
            name: {day: min(prices) for day, prices in days.items()}
            for name, days in grouped.items()
        }

    @staticmethod
    def price_trend(quotes: list[dict[str, Any]]) -> dict[str, Any]:
        """The direction of the market, as the typical supplier's move.

        Per *supplier*, not per daily cheapest. The daily minimum is whoever
        happened to quote lowest, so one supplier re-pricing — or one supplier
        dropping out of the comparison — used to flip the whole market's
        direction. The signal here is the median change across the suppliers who
        quoted on both of the two most recent days, which a single move cannot
        swing, and it only counts once it clears the quote's own resolution.

        Benchmark sources are held out here exactly as they are from the
        snapshot: Fueltool's UK average is context for the market, not a
        participant in it.
        """
        market_quotes = [
            quote for quote in quotes if quote.get("source") not in BENCHMARK_SOURCES
        ]
        daily = AnalyticsService._daily_cheapest(market_quotes)
        if not daily:
            return {
                "direction": "insufficient_data",
                "days": [],
                "cheapest_per_day": [],
                "latest_change": None,
                "moving_average_7d": None,
                "suppliers_compared": 0,
                "change_by_supplier": {},
                "latest_day": None,
                "previous_day": None,
            }

        days = sorted(daily)
        cheapest_per_day = [round(daily[day], 4) for day in days]

        latest_day = days[-1]
        previous_day = days[-2] if len(days) >= 2 else None

        change_by_supplier: dict[str, float] = {}
        if previous_day is not None:
            for name, series in AnalyticsService._daily_by_supplier(market_quotes).items():
                if latest_day in series and previous_day in series:
                    change_by_supplier[name] = round(
                        series[latest_day] - series[previous_day], 4
                    )

        latest_change: float | None = None
        direction = "stable"
        if change_by_supplier:
            latest_change = round(median(change_by_supplier.values()), 4)
            if latest_change >= TREND_THRESHOLD:
                direction = "rising"
            elif latest_change <= -TREND_THRESHOLD:
                direction = "falling"

        moving_average_7d = round(mean(cheapest_per_day[-7:]), 4) if cheapest_per_day else None

        return {
            "direction": direction,
            "days": days,
            "cheapest_per_day": cheapest_per_day,
            "latest_change": latest_change,
            "moving_average_7d": moving_average_7d,
            # The basis behind the verdict, so it can be audited rather than
            # taken on trust.
            "suppliers_compared": len(change_by_supplier),
            "change_by_supplier": change_by_supplier,
            "latest_day": latest_day,
            "previous_day": previous_day,
        }

    @staticmethod
    def recommendation(snapshot: dict[str, Any], trend: dict[str, Any]) -> str:
        """State the cheapest offer, and how the market moved if it did.

        Deliberately not "prices are rising, buy soon": the move is the typical
        change across the suppliers who quoted on both days, reported with that
        basis rather than asserted as a market-wide direction the data cannot
        support.
        """
        cheapest = snapshot.get("cheapest_supplier")
        if not cheapest:
            return "No successful quotes yet - run `oilwatch quote-all` to collect prices."
        name = cheapest["name"]
        price = cheapest["price_per_liter"]
        cheapest_bit = f"{name} is cheapest at £{price:.4f}/L"
        direction = trend.get("direction")
        change = trend.get("latest_change")
        compared = trend.get("suppliers_compared") or 0
        suppliers = f"{compared} supplier" + ("" if compared == 1 else "s")

        if direction in {"rising", "falling"} and change is not None:
            verb = "rose" if direction == "rising" else "fell"
            return (
                f"{cheapest_bit}. The typical quote {verb} {abs(change) * 100:.1f}p/L "
                f"across {suppliers} quoting both days."
            )
        if compared:
            return (
                f"{cheapest_bit}. Quotes are little changed across {suppliers} "
                f"quoting both days."
            )
        return f"{cheapest_bit}. Not enough quotes to call a direction."

    @staticmethod
    def build_chart(quotes: list[dict[str, Any]], output_path: Path) -> Path:
        grouped: dict[str, list[float]] = defaultdict(list)
        for quote in AnalyticsService._ok_priced(quotes):
            grouped[AnalyticsService._day_of(quote)].append(float(quote["price_per_liter"]))
        if not grouped:
            raise ValueError("No successful quotes available for charting.")

        days = sorted(grouped)
        cheapest = [min(grouped[day]) for day in days]
        averages = [mean(grouped[day]) for day in days]
        variances = [pvariance(grouped[day]) if len(grouped[day]) > 1 else 0.0 for day in days]

        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig, ax1 = plt.subplots(figsize=(12, 6))
        ax1.plot(days, cheapest, label="Cheapest", color="#0b6e4f", linewidth=2)
        ax1.plot(days, averages, label="Average", color="#d1495b", linewidth=2)
        ax1.set_ylabel("Price per liter (GBP)")
        ax1.tick_params(axis="x", rotation=45)

        ax2 = ax1.twinx()
        ax2.plot(days, variances, label="Variance", color="#00798c", linestyle="--", linewidth=2)
        ax2.set_ylabel("Variance")

        lines = ax1.get_lines() + ax2.get_lines()
        ax1.legend(lines, [line.get_label() for line in lines], loc="upper left")
        ax1.set_title("Heating Oil Market Summary")
        ax1.grid(alpha=0.2)
        fig.tight_layout()
        fig.savefig(output_path, dpi=160)
        plt.close(fig)
        return output_path

    @staticmethod
    def build_time_series_chart(
        quotes: list[dict[str, Any]],
        output_path: Path,
        brent: list[dict[str, Any]] | None = None,
    ) -> Path:
        """Plot each supplier's price over time as its own line.

        One line per supplier, ordered by observation time, so the user can see
        how each supplier's price moves and spot a favourable moment to buy.
        When ``brent`` (rows with ``observed_at`` and ``price_usd_per_barrel``)
        is supplied, a Brent crude line is drawn on a secondary $/barrel axis.
        """
        series: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
        for quote in AnalyticsService._ok_priced(quotes):
            try:
                observed = datetime.fromisoformat(quote["observed_at"])
            except (TypeError, ValueError):
                continue
            series[quote["supplier_name"]].append((observed, float(quote["price_per_liter"])))

        brent_points: list[tuple[datetime, float]] = []
        for row in brent or []:
            try:
                observed = datetime.fromisoformat(row["observed_at"])
                price = float(row["price_usd_per_barrel"])
            except (TypeError, ValueError, KeyError):
                continue
            brent_points.append((observed, price))

        if not series and not brent_points:
            raise ValueError("No successful quotes available for charting.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig, ax = plt.subplots(figsize=(12, 6))
        for name, points in series.items():
            points.sort(key=lambda point: point[0])
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            ax.plot(xs, ys, marker="o", linewidth=2, label=name)

        ax.set_xlabel("Date")
        ax.set_ylabel("Price per litre (GBP, inc. VAT)")
        ax.set_title("Heating Oil Price Time Series by Supplier")
        ax.grid(alpha=0.2)

        if brent_points:
            brent_points.sort(key=lambda point: point[0])
            bx = [point[0] for point in brent_points]
            by = [point[1] for point in brent_points]
            ax_brent = ax.twinx()
            ax_brent.plot(
                bx, by, color="#1f77b4", linestyle="--", linewidth=2, label="Brent crude ($/barrel)"
            )
            ax_brent.set_ylabel("Brent crude ($/barrel)")
            ax_brent.grid(False)
            lines = ax.get_lines() + ax_brent.get_lines()
            ax.legend(lines, [line.get_label() for line in lines], loc="upper left", fontsize=9)
        else:
            ax.legend(loc="upper left", fontsize=9)

        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(output_path, dpi=160)
        plt.close(fig)
        return output_path

