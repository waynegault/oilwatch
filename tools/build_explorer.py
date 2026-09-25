"""Build the OilWatch data explorer: a self-contained, offline HTML page.

Reads the live database read-only and writes two files beside the generated
charts: a standalone page that opens from disk, and the body-only fragment the
Artifact publisher wants. Nothing is written to the database.

    python tools/build_explorer.py [--check]

``--check`` prints the payload summary and stops, without writing the page.

One network call, at build time: the USD->GBP reference rate the page converts
Brent with. Fetched rather than assumed, so the page carries a real rate and its
date; when the fetch fails the page falls back to showing Brent in dollars.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oilwatch.config import CHECKOUT_ROOT  # noqa: E402
from oilwatch.models import utcnow_naive  # noqa: E402
from oilwatch.service import OilWatchApp  # noqa: E402

TEMPLATE = Path(__file__).with_name("explorer_template.html")
STANDALONE = CHECKOUT_ROOT / "data" / "oilwatch-explorer.html"
FRAGMENT = CHECKOUT_ROOT / "data" / "oilwatch-explorer.fragment.html"

#: A barrel is a volume: 158.987 litres of anything. Crude is not kerosene, so
#: the rescaled Brent line is context rather than a like-for-like benchmark.
LITRES_PER_BARREL = 158.987

#: How recent "recent" is for the league table's second reading: the last seven
#: days on which anything was quoted. That is form, where the total wins figure
#: is history — the two differ sharply for a supplier who stopped being cheap in
#: 2013 or stopped quoting in 2025.
RECENT_DAYS = 7

#: Keyless source of the ECB's USD->GBP reference rate. The page converts Brent
#: with it, so its date matters as much as its value.
FX_URL = "https://api.frankfurter.app/latest?from=USD&to=GBP"


def fetch_usd_gbp() -> dict | None:
    """The USD->GBP reference rate, or None when there is no network.

    Fetched rather than assumed: an invented rate would silently mis-scale every
    Brent comparison on the page. The date and source travel with it so a stale
    rate is visible instead of implied, and no rate is better than a wrong one -
    the page falls back to showing Brent in dollars.
    """
    try:
        response = httpx.get(FX_URL, follow_redirects=True, timeout=30)
        response.raise_for_status()
        body = response.json()
        return {
            "rate": float(body["rates"]["GBP"]),
            "as_of": body.get("date"),
            "source": "ECB reference rate via frankfurter.app",
            "url": FX_URL,
        }
    except Exception as exc:  # noqa: BLE001 - report it and carry on without
        print(f"fx: unavailable ({type(exc).__name__}: {exc})")
        return None


def day_of(observed_at: str) -> str:
    return observed_at[:10]


def rank_for_display(rows: list[dict]) -> list[dict]:
    """The current-window rows in the order a reader should meet them.

    Cheapest *effective* price first — the same basis `cheapest` names the winner
    on, so the table cannot disagree with the card above it. It could: the rows
    arrive from `current_prices` in headline order (``db.latest_quotes`` orders on
    ``price_per_liter``), so a code that changed who leads left the card naming one
    supplier and the top row another.

    A benchmark sorts last whatever it costs. Fueltool's UK average is routinely
    below every real quote, and a table whose first row is a figure rather than a
    supplier is the misreading ``kind`` exists to prevent.
    """

    def key(row: dict) -> tuple[int, float]:
        effective = row.get("effective_price_per_liter")
        price = effective if effective is not None else row["price_per_liter"]
        return (1 if row.get("kind") == "benchmark" else 0, float(price))

    return sorted(rows, key=key)


def _without_phone(rows: list[dict]) -> list[dict]:
    """The same rows with any phone removed from their contact block.

    The service puts ``contact: {phone, email, url}`` on the rows these lists are
    built from, and a number in a page whose column says "where to ask" is a
    route rather than a field - the reason the suppliers list above carries no
    phone either. The number stays where it belongs: the register, the database
    column and `status`. The address is kept, because asking by email is a route
    the app does take.
    """
    return [_strip_phone(row) for row in rows or []]


def _strip_phone(value: Any) -> Any:
    """Any payload value, with every ``contact.phone`` inside it blanked.

    Blanked rather than deleted: the key is part of the shape the page and the
    service share, and a reader of the file should see that a phone was
    deliberately not carried rather than wonder whether one exists.
    """
    if isinstance(value, dict):
        copy = {key: _strip_phone(child) for key, child in value.items()}
        contact = copy.get("contact")
        if isinstance(contact, dict) and contact.get("phone"):
            copy["contact"] = {**contact, "phone": None}
        return copy
    if isinstance(value, list):
        return [_strip_phone(child) for child in value]
    return value


def build_payload(app: OilWatchApp) -> dict:
    quotes = [row for row in app.db.all_quotes() if row["status"] == "ok" and row["price_per_liter"]]
    suppliers = app.db.list_suppliers()
    orders = app.purchases()

    by_supplier: dict[str, list[tuple[str, float]]] = {}
    for row in quotes:
        by_supplier.setdefault(row["supplier_name"], []).append(
            (day_of(row["observed_at"]), float(row["price_per_liter"]))
        )
    for points in by_supplier.values():
        points.sort()

    # Per-day market figures and the historical league table.
    per_day: dict[str, list[tuple[str, float]]] = {}
    for name, points in by_supplier.items():
        for day, price in points:
            per_day.setdefault(day, []).append((name, price))
    days = sorted(per_day)
    market = [
        [
            day,
            round(min(price for _, price in per_day[day]), 4),
            round(statistics.fmean(price for _, price in per_day[day]), 4),
            round(
                statistics.pvariance([price for _, price in per_day[day]]), 6
            )
            if len(per_day[day]) > 1
            else 0.0,
            len(per_day[day]),
        ]
        for day in days
    ]
    recent_cutoff = days[-RECENT_DAYS:] if len(days) > RECENT_DAYS else days
    wins: dict[str, int] = {}
    recent_wins: dict[str, int] = {}
    for day in days:
        best = min(per_day[day], key=lambda pair: pair[1])[0]
        wins[best] = wins.get(best, 0) + 1
        if day in recent_cutoff:
            recent_wins[best] = recent_wins.get(best, 0) + 1

    league = []
    for name, points in sorted(by_supplier.items()):
        prices = [price for _, price in points]
        best_day, best_price = min(points, key=lambda pair: pair[1])
        last_day, last_price = points[-1]
        spread = max(prices) - min(prices)
        stdev = statistics.pstdev(prices) if len(prices) > 1 else 0.0
        league.append(
            {
                "name": name,
                "quotes": len(points),
                "days": len({day for day, _ in points}),
                "first": points[0][0],
                "last": last_day,
                "best": round(best_price, 4),
                "best_at": best_day,
                "latest": round(last_price, 4),
                "mean": round(statistics.fmean(prices), 4),
                "min": round(min(prices), 4),
                "max": round(max(prices), 4),
                "spread": round(spread, 4),
                "stdev": round(stdev, 4),
                "cv": round(stdev / statistics.fmean(prices), 4) if prices else None,
                "wins": wins.get(name, 0),
                "recent_wins": recent_wins.get(name, 0),
                "share": round(wins.get(name, 0) / len(days), 4),
            }
        )

    # Purchases, each against the market it landed in.
    priced_by_day = {row[0]: row for row in market}
    purchases = []
    for order in orders:
        day = day_of(order["created_at"])
        context = priced_by_day.get(day)
        purchases.append(
            {
                **order,
                "market_day": day,
                "market_best": context[1] if context else None,
                "market_mean": context[2] if context else None,
                "suppliers_that_day": context[4] if context else None,
            }
        )

    envelope = app.current_prices()
    current = {
        "as_of": envelope["as_of"],
        "window_days": envelope["window_days"],
        "quotes": rank_for_display(
            [
                {
                    "name": row["supplier_name"],
                    "website": row["website"],
                    "order_page": row.get("order_page"),
                    "price_per_liter": row["price_per_liter"],
                    "effective_price_per_liter": row.get("effective_price_per_liter"),
                    "observed_at": row["observed_at"],
                    "valid_until": row.get("valid_until"),
                    "discount": row.get("discount"),
                    "reason": row.get("reason"),
                    # The page has to be able to say "this row is a figure, not a
                    # supplier" and to name the winner's own row — the two things a
                    # reader gets wrong from a bare price table.
                    "kind": row.get("kind"),
                    "order_channel": row.get("order_channel"),
                }
                for row in envelope["quotes"]
            ]
        ),
        "no_quote_suppliers": _without_phone(envelope["no_quote_suppliers"]),
        "failed_suppliers": _without_phone(envelope["failed_suppliers"]),
        "excluded_suppliers": _without_phone(envelope["excluded_suppliers"]),
        "not_refreshed_suppliers": _without_phone(envelope["not_refreshed_suppliers"]),
        "never_quoted": _without_phone(envelope["never_quoted"]),
    }
    snapshot = _strip_phone(app.status()["market_snapshot"])

    # Who has been asked for a price, and where each ask stands. `status` answers
    # this for an agent as `awaiting_reply`; the page has to answer it for a
    # reader, and "waiting" and "the ask came back with nothing" are different
    # answers to give.
    empty_ask = {
        row.get("name") or row.get("supplier_name")
        for row in (envelope["no_quote_suppliers"] or [])
    }
    enquiries = [
        {
            "name": row["supplier_name"],
            "channel": row["channel"],
            "requested_at": row["requested_at"],
            "asked": row["asked"],
            "note": row["note"],
            "state": (
                "answered"
                if row["answered_at"]
                else "empty"
                if row["supplier_name"] in empty_ask
                else "awaiting"
            ),
            "price_per_liter": row["price_per_liter"],
            "price_at": row["price_at"],
            "price_source": row["price_source"],
        }
        for row in app.db.quote_requests_by_supplier()
    ]

    return {
        "enquiries": enquiries,
        "meta": {
            "generated_at": utcnow_naive().isoformat(timespec="seconds"),
            "litres_per_barrel": LITRES_PER_BARREL,
            "recent_days": RECENT_DAYS,
            "quotes": len(quotes),
            "brent_points": len(app.db.all_brent()),
            "days": len(days),
        },
        # Fetched at build time, so the page converts Brent to pounds on its own.
        "fx": fetch_usd_gbp(),
        "suppliers": [
            {
                "id": row["id"],
                "name": row["name"],
                "website": row["website"],
                "order_page": row.get("connector_config", {}).get("order_page"),
                "connector_type": row["connector_type"],
                # No phone. The register, the `contact.phone` field and
                # raw_payload keep a number as data, but a page whose column says
                # "where to ask" is offering a route, and the app asks by form or
                # by email only - the decision of 2026-09-22.
                "email": row.get("email"),
                "status": row.get("status"),
            }
            for row in suppliers
        ],
        "series": {name: [[day, round(price, 4)] for day, price in points] for name, points in by_supplier.items()},
        "market": market,
        "league": league,
        "brent": [
            [day_of(row["observed_at"]), round(float(row["price_usd_per_barrel"]), 2)]
            for row in app.db.all_brent()
        ],
        "current": current,
        "snapshot": snapshot,
        "purchases": purchases,
        "discounts": app.db.active_discounts(),
    }


def summarise(payload: dict) -> None:
    meta = payload["meta"]
    print(f"generated_at  {meta['generated_at']}")
    print(f"quotes        {meta['quotes']} priced rows across {meta['days']} days")
    print(f"brent         {meta['brent_points']} points")
    print(f"series        {len(payload['series'])} suppliers")
    print(f"league rows   {len(payload['league'])}")
    print(f"purchases     {len(payload['purchases'])}")
    print("\nleague (wins / quotes / best / latest):")
    for row in sorted(payload["league"], key=lambda r: -r["wins"]):
        print(
            f"  {row['name']:38s} wins {row['wins']:4d}  recent {row['recent_wins']:3d}"
            f"  quotes {row['quotes']:4d}  best {row['best']:.4f}  latest {row['latest']:.4f}"
        )
    brent = payload["brent"]
    if brent:
        print(f"\nbrent range   ${min(p for _, p in brent):.2f} .. ${max(p for _, p in brent):.2f} per barrel")
    fx = payload["fx"]
    print(
        "fx            "
        + (
            f"{fx['rate']} GBP per USD as of {fx['as_of']} ({fx['source']})"
            if fx
            else "unavailable - Brent stays in dollars on the page"
        )
    )
    print(f"current       {len(payload['current']['quotes'])} priced, "
          f"{len(payload['current']['no_quote_suppliers'])} no quote, "
          f"{len(payload['current']['failed_suppliers'])} failed")
    for order in payload["purchases"]:
        print(
            f"purchase      {order['supplier_name']} {order['quantity_liters']}L "
            f"£{order['agreed_price_per_liter']}/L total £{order['total_price']} "
            f"on {order['created_at'][:10]} (market best {order['market_best']})"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="print the payload summary only")
    args = parser.parse_args()

    payload = build_payload(OilWatchApp())
    summarise(payload)

    if args.check:
        return

    fragment = TEMPLATE.read_text(encoding="utf-8").replace(
        "__PAYLOAD__", json.dumps(payload, separators=(",", ":"))
    )
    FRAGMENT.write_text(fragment, encoding="utf-8")
    standalone = (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>OilWatch data explorer</title>\n</head>\n<body>\n"
        f"{fragment}\n</body>\n</html>\n"
    )
    STANDALONE.write_text(standalone, encoding="utf-8")
    kb = len(standalone) / 1024
    print(f"\nwrote {STANDALONE} ({kb:.0f} KB)")
    print(f"wrote {FRAGMENT} ({len(fragment) / 1024:.0f} KB)")

    # Parse-check the inline JavaScript. A syntax error leaves the page blank and
    # is invisible to every other check available here.
    script = fragment.split("<script>")[-1].split("</script>")[0]
    js = Path(__file__).with_name("explorer_inline.js")
    js.write_text(script, encoding="utf-8")
    result = subprocess.run(
        ["node", "--check", str(js)], capture_output=True, text=True, check=False
    )
    print("node --check:", "ok" if result.returncode == 0 else result.stderr.strip()[:400])
    js.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
