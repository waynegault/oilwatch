from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, pvariance
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


class AnalyticsService:
    @staticmethod
    def latest_market_snapshot(latest_quotes: list[dict[str, Any]]) -> dict[str, Any]:
        if not latest_quotes:
            return {
                "cheapest_supplier": None,
                "average_price_per_liter": None,
                "variance": None,
                "quotes_considered": 0,
            }
        prices = [row["price_per_liter"] for row in latest_quotes if row["price_per_liter"] is not None]
        if not prices:
            return {
                "cheapest_supplier": None,
                "average_price_per_liter": None,
                "variance": None,
                "quotes_considered": 0,
            }
        priced = [row for row in latest_quotes if row["price_per_liter"] is not None]
        cheapest = min(priced, key=lambda row: row["price_per_liter"])
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
            },
            "average_price_per_liter": round(mean(prices), 4),
            "variance": round(pvariance(prices), 6) if len(prices) > 1 else 0.0,
            "quotes_considered": len(prices),
        }

    @staticmethod
    def _daily_cheapest(quotes: list[dict[str, Any]]) -> dict[str, float]:
        """Map ``YYYY-MM-DD`` -> cheapest successful price that day."""
        grouped: dict[str, list[float]] = defaultdict(list)
        for quote in quotes:
            if quote["status"] != "ok" or quote["price_per_liter"] is None:
                continue
            day = datetime.fromisoformat(quote["observed_at"]).date().isoformat()
            grouped[day].append(float(quote["price_per_liter"]))
        return {day: min(prices) for day, prices in grouped.items()}

    @staticmethod
    def price_trend(quotes: list[dict[str, Any]]) -> dict[str, Any]:
        """Compute the direction of the cheapest daily price.

        Returns a 7-day moving average and the day-over-day change, so the user
        can judge whether to buy now or wait for a better price.
        """
        daily = AnalyticsService._daily_cheapest(quotes)
        if not daily:
            return {
                "direction": "insufficient_data",
                "days": [],
                "cheapest_per_day": [],
                "latest_change": None,
                "moving_average_7d": None,
            }

        days = sorted(daily)
        cheapest_per_day = [round(daily[day], 4) for day in days]

        latest_change: float | None = None
        direction = "stable"
        if len(cheapest_per_day) >= 2:
            latest_change = round(cheapest_per_day[-1] - cheapest_per_day[-2], 4)
            if latest_change > 0.0001:
                direction = "rising"
            elif latest_change < -0.0001:
                direction = "falling"

        moving_average_7d = round(mean(cheapest_per_day[-7:]), 4) if cheapest_per_day else None

        return {
            "direction": direction,
            "days": days,
            "cheapest_per_day": cheapest_per_day,
            "latest_change": latest_change,
            "moving_average_7d": moving_average_7d,
        }

    @staticmethod
    def recommendation(snapshot: dict[str, Any], trend: dict[str, Any]) -> str:
        """Produce a short human-readable buy/hold recommendation."""
        cheapest = snapshot.get("cheapest_supplier")
        if not cheapest:
            return "No successful quotes yet - run `oilwatch quote-all` to collect prices."
        name = cheapest["name"]
        price = cheapest["price_per_liter"]
        direction = trend.get("direction")

        if direction == "falling":
            return f"Prices are falling - {name} is cheapest at £{price:.4f}/L. Consider waiting for a lower price."
        if direction == "rising":
            return f"Prices are rising - {name} is cheapest at £{price:.4f}/L. Consider buying soon to lock in the price."
        return f"Prices are stable - {name} is cheapest at £{price:.4f}/L."

    @staticmethod
    def build_chart(quotes: list[dict[str, Any]], output_path: Path) -> Path:
        grouped: dict[str, list[float]] = defaultdict(list)
        for quote in quotes:
            if quote["status"] != "ok" or quote["price_per_liter"] is None:
                continue
            day = datetime.fromisoformat(quote["observed_at"]).date().isoformat()
            grouped[day].append(float(quote["price_per_liter"]))
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
        for quote in quotes:
            if quote["status"] != "ok" or quote["price_per_liter"] is None:
                continue
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

