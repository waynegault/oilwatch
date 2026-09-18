from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from oilwatch.analytics import AnalyticsService
from oilwatch.config import CHECKOUT_ROOT, Settings, load_settings, load_supplier_overrides
from oilwatch.db import Database
from oilwatch.discovery import DiscoveryService
from oilwatch.geo import GeoService
from oilwatch.logging_setup import get_logger
from oilwatch.models import utcnow_naive
from oilwatch.quotes import QuoteService

log = get_logger("service")


class OilWatchApp:
    def __init__(self, root: Path | None = None) -> None:
        # The checkout, not the process working directory: the CLI builds this
        # with no root, and a cwd default meant the same command run from another
        # directory read a different install — or failed outright when that
        # directory had no config/settings.json.
        self.root = root or CHECKOUT_ROOT
        self.settings: Settings = load_settings(self.root)
        self.db = Database(self.settings.database_path)
        self.geo = GeoService()
        self.discovery = DiscoveryService(self.settings, self.geo)
        self.quotes = QuoteService(self.settings.currency, self.settings.home.label)
        self.analytics = AnalyticsService()

    def init(self) -> dict[str, Any]:
        self.db.init_schema()
        imported = 0
        for supplier in load_supplier_overrides(self.root):
            self.db.upsert_supplier(supplier)
            imported += 1
        return {
            "database_path": str(self.settings.database_path),
            "imported_overrides": imported,
        }

    def discover_suppliers(self) -> dict[str, Any]:
        self.db.init_schema()
        candidates = self.discovery.discover()
        stored = 0
        active_websites: list[str] = []
        for candidate in candidates:
            self.db.upsert_supplier(candidate.to_record())
            active_websites.append(candidate.website)
            stored += 1
        self.db.mark_missing_suppliers_inactive(active_websites)
        return {
            "stored_suppliers": stored,
            "radius_miles": self.settings.radius_miles,
            "home": self.settings.home.label,
        }

    def suppliers(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        self.db.init_schema()
        return self.db.list_suppliers(include_inactive=include_inactive)

    def quote_supplier(
        self,
        supplier_id: int,
        postcode: str | None = None,
        prefer_browser: bool = False,
    ) -> dict[str, Any]:
        self.db.init_schema()
        supplier = self.db.get_supplier(supplier_id)
        if not supplier:
            raise ValueError(f"Unknown supplier id: {supplier_id}")
        result = self.quotes.quote_supplier(
            supplier,
            self.settings.quote_quantity_liters,
            postcode=postcode,
            prefer_browser=prefer_browser,
        )
        self.db.record_quote(result.to_record())
        return result.to_record()

    def quote_all(
        self,
        postcode: str | None = None,
        prefer_browser: bool = False,
        max_workers: int | None = None,
    ) -> list[dict[str, Any]]:
        self.db.init_schema()
        suppliers = self.db.list_suppliers(include_inactive=False)
        workers = max(1, self.settings.quote_max_workers if max_workers is None else max_workers)

        def quote_one(supplier: dict[str, Any]) -> dict[str, Any]:
            try:
                result = self.quotes.quote_supplier(
                    supplier,
                    self.settings.quote_quantity_liters,
                    postcode=postcode,
                    prefer_browser=prefer_browser,
                )
                return result.to_record()
            except Exception as exc:  # noqa: BLE001
                log.warning("Quote collection failed for %s: %s", supplier["name"], exc)
                return {
                    "supplier_id": supplier["id"],
                    "supplier_name": supplier["name"],
                    "observed_at": utcnow_naive().isoformat(),
                    "quantity_liters": self.settings.quote_quantity_liters,
                    "status": "error",
                    "price_per_liter": None,
                    "total_price": None,
                    "currency": self.settings.currency,
                    "source": supplier.get("connector_type", "unknown"),
                    "notes": str(exc),
                    # Classified rather than left to the exception's wording:
                    # the message is prose, and this is the one reason every
                    # raising path shares.
                    "reason": "site_error",
                    "raw_payload": {},
                }

        if workers == 1 or len(suppliers) < 2:
            payloads = [quote_one(supplier) for supplier in suppliers]
        else:
            # Each quote is a browser launch of 10-30s, so a sequential run
            # scaled linearly with the supplier count. Several run at once, but
            # the pool is capped: there is no per-supplier rate limiting, and a
            # wide fan-out risks the CAPTCHA/bot heuristics the connectors
            # already work around. ``map`` keeps results in supplier order.
            with ThreadPoolExecutor(max_workers=min(workers, len(suppliers))) as pool:
                payloads = list(pool.map(quote_one, suppliers))

        results: list[dict[str, Any]] = []
        for supplier, payload in zip(suppliers, payloads, strict=True):
            # Recorded here, in the one thread, so concurrent quotes cannot
            # contend for the single SQLite file.
            self.db.record_quote(payload)

            # Errors are worth a warning; the routine manual_action_required
            # results stay at debug so a normal run does not produce a wall of
            # lines for suppliers that only ever quote by phone.
            status = payload.get("status")
            if status == "error":
                log.warning("%s returned an error: %s", supplier["name"], payload.get("notes", ""))
            elif status != "ok":
                log.debug("%s: %s", supplier["name"], payload.get("notes") or status)
            results.append(payload)

        # Tell the owner directly rather than leaving failures to be found in a
        # log file. Batched into one toast, and never fatal (see oilwatch.notify).
        failed = [row["supplier_name"] for row in results if row.get("status") == "error"]
        if failed:
            from oilwatch.notify import notify_errors

            notify_errors(failed)
        return results

    def _current_quotes(self) -> list[dict[str, Any]]:
        """Latest successful quote per supplier, ignoring stale history.

        Without the age window a supplier whose only priced quote came from the
        historical spreadsheet import would outrank suppliers quoted today.
        """
        return self.db.latest_quotes(max_age_days=self.settings.max_quote_age_days)

    def _excluded_suppliers(self) -> list[dict[str, Any]]:
        """Suppliers the age window drops, with the last price each gave.

        Quotes last about a day, so the window is deliberately tight; naming the
        suppliers it holds back keeps a thin snapshot legible as "not re-quoted
        yet" rather than looking like a scrape that failed.
        """
        return [
            {
                "name": row["supplier_name"],
                "last_quote_at": row["observed_at"],
                "last_price_per_liter": row["price_per_liter"],
            }
            for row in self.db.stale_quotes(max_age_days=self.settings.max_quote_age_days)
        ]

    def _not_refreshed_suppliers(self) -> list[dict[str, Any]]:
        """Suppliers still priced inside the window but not re-quoted since.

        A supplier whose latest attempt failed keeps the price it gave earlier —
        the window is a day — so the comparison can otherwise mix the current
        run's quotes with an older figure and present it as today's. Naming them
        lets a report say which prices are older than the run behind them.
        """
        return [
            {
                "name": row["supplier_name"],
                "website": row["website"],
                "price_per_liter": row["price_per_liter"],
                "last_quote_at": row["observed_at"],
                "last_attempt_at": row["last_attempt_at"],
                "last_attempt_status": row["last_attempt_status"],
                # The machine-readable companion to the note below, so a consumer
                # can branch on *why* a supplier has no fresh price instead of
                # reading prose to find out.
                "last_attempt_reason": row["last_attempt_reason"],
                "last_attempt_note": row["last_attempt_note"],
            }
            for row in self.db.not_refreshed_quotes(max_age_days=self.settings.max_quote_age_days)
        ]

    def _with_effective_prices(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Attach the best applicable discount code and the resulting price.

        The stored price is already inclusive of 5% VAT, so ``effective_*`` is a
        like-for-like comparison: it is what the order would actually cost once
        any code is applied. The headline price is kept alongside it, because a
        code may be single-use or non-combinable.
        """
        from oilwatch.discounts import DiscountOffer, best_discount_for, effective_price_per_litre

        configured_quantity = self.settings.quote_quantity_liters
        by_supplier: dict[Any, list[DiscountOffer]] = {}
        for record in self.db.active_discounts():
            by_supplier.setdefault(record.get("supplier_id"), []).append(DiscountOffer.from_record(record))

        enriched_rows: list[dict[str, Any]] = []
        for row in rows:
            enriched = dict(row)
            offers = by_supplier.get(row.get("supplier_id")) or []
            litres = int(row.get("quantity_liters") or configured_quantity)
            best = best_discount_for(offers, litres) if offers else None

            # The page a human would order from, when the supplier's own config
            # names one (`order_page`). Three deliberate choices here. It is not
            # taken from the quote's payload, because that URL is whatever the
            # connector fetches and for some suppliers it is an API endpoint
            # (Highland Fuels' getoffers.php), so promoting it would put a link an
            # agent then cites onto a row that cannot be ordered from it. The key
            # is not `order_url`, which the shipped config example already uses
            # for the POST target of the retired automated ordering path. And an
            # absent value means "not recorded", not "no page exists".
            config = json.loads(enriched.pop("connector_config_json", None) or "{}")
            enriched["order_page"] = config.get("order_page")

            price = row.get("price_per_liter")
            if price is not None:
                effective = effective_price_per_litre(float(price), litres, best)
                enriched["effective_price_per_liter"] = effective
                enriched["effective_total_price"] = round(effective * litres, 2)

            enriched["discount"] = (
                {
                    "code": best.code,
                    "amount_gbp": best.amount_gbp,
                    "expires_at": best.expires_at.isoformat() if best.expires_at else None,
                }
                if best
                else None
            )
            enriched_rows.append(enriched)
        return enriched_rows

    def cheapest(self) -> dict[str, Any]:
        self.db.init_schema()
        return self.analytics.latest_market_snapshot(
            self._with_effective_prices(self._current_quotes()),
            excluded_suppliers=self._excluded_suppliers(),
            not_refreshed_suppliers=self._not_refreshed_suppliers(),
            window_days=self.settings.max_quote_age_days,
        )

    def _supplier_attempt_outcomes(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """The last ask per supplier, split into the two ways it can come back empty.

        Reported separately because they call for different next actions: a
        supplier that gave no price is often doing exactly what it does — no web
        quote to read, so the contact details are the answer — while one whose
        retrieval failed is a fault worth looking at. ``reason`` says which,
        without reading the note.
        """
        no_quote: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for row in self.db.latest_attempts():
            entry = {
                "name": row["supplier_name"],
                "website": row["website"],
                "last_attempt_at": row["observed_at"],
                "reason": row["reason"],
                "note": row["notes"],
            }
            if row["status"] == "error":
                failed.append(entry)
            elif row["status"] != "ok":
                no_quote.append(entry)
        return no_quote, failed

    def refresh_recently_done(self, minutes: int) -> dict[str, Any] | None:
        """Whether a sweep ran within ``minutes``, and when.

        Measured from the newest observation of *any* kind — "when did we last go
        and look?" — so a caller can refuse to scrape again without keeping any
        state of its own. Returns ``{refreshed_at, minutes_ago}``, or None when
        nothing is on record, when the sweep is older than ``minutes``, or when
        the stored timestamp cannot be parsed: "no age to report" and "zero
        minutes ago" are not the same thing, and only the second should stop a
        refresh.
        """
        newest = self.db.newest_observation()
        if not newest:
            return None
        try:
            observed = datetime.fromisoformat(newest)
        except ValueError:
            return None
        age_minutes = (utcnow_naive() - observed).total_seconds() / 60
        if age_minutes >= minutes:
            return None
        return {"refreshed_at": newest, "minutes_ago": round(age_minutes, 1)}

    def current_prices(self) -> dict[str, Any]:
        """The latest quote per supplier, with the scope it was read against.

        An envelope rather than a bare list, because an empty list cannot say
        *why* it is empty: the market may not have been refreshed, the window may
        be tight, the database may be new, or a supplier may never have been
        quoted at all. Those are four different next actions, and naming them is
        the difference between a first call that is a dead end and one that
        explains itself.
        """
        self.db.init_schema()
        rows = self._with_effective_prices(self._current_quotes())
        observed = [row["observed_at"] for row in rows if row.get("observed_at")]
        no_quote, failed = self._supplier_attempt_outcomes()
        return {
            # The freshest observation in `quotes`, so the whole response can be
            # aged at a glance; None when there is nothing to age.
            "as_of": max(observed) if observed else None,
            "window_days": self.settings.max_quote_age_days,
            "quotes": rows,
            "excluded_suppliers": self._excluded_suppliers(),
            "not_refreshed_suppliers": self._not_refreshed_suppliers(),
            "never_quoted": self.db.unquoted_suppliers(),
            # The two ways a supplier can be missing from `quotes` because the ask
            # itself came back empty. Kept apart from the lists above, which name
            # prices that a window or a failed attempt held back: these name what
            # the most recent ask did.
            "no_quote_suppliers": no_quote,
            "failed_suppliers": failed,
        }

    def status(self) -> dict[str, Any]:
        self.db.init_schema()
        snapshot = self.analytics.latest_market_snapshot(
            self._with_effective_prices(self._current_quotes()),
            excluded_suppliers=self._excluded_suppliers(),
            not_refreshed_suppliers=self._not_refreshed_suppliers(),
            window_days=self.settings.max_quote_age_days,
        )
        trend = self.analytics.price_trend(self.db.all_quotes())
        no_quote, failed = self._supplier_attempt_outcomes()
        return {
            "market_snapshot": snapshot,
            "trend": trend,
            "recommendation": self.analytics.recommendation(snapshot, trend),
            # Carried here so "have I already ordered, and what did I pay?" is
            # answerable without a second call.
            "last_purchase": next(iter(self.purchases(limit=1)), None),
            # And which suppliers the last sweep could not price, split by whether
            # they had no quote to give or the retrieval failed — the difference
            # between an expected gap and a fault worth looking at.
            "no_quote_suppliers": no_quote,
            "failed_suppliers": failed,
        }

    def monitor_email(self) -> dict[str, Any]:
        """Poll the inbox for supplier replies, record quotes, delete emails."""
        from oilwatch.graph_email import GraphEmailMonitor

        self.db.init_schema()
        try:
            recorded = GraphEmailMonitor().run(self)
        except Exception as exc:  # noqa: BLE001 - don't let a transient failure crash the scheduler
            return {"recorded": [], "error": str(exc)}
        return {"recorded": recorded}

    def chart(self) -> str:
        self.db.init_schema()
        path = self.analytics.build_chart(self.db.all_quotes(), self.settings.chart_path)
        return str(path)

    def time_series_chart(self) -> str:
        self.db.init_schema()
        path = self.analytics.build_time_series_chart(
            self.db.all_quotes(), self.settings.time_series_chart_path, brent=self.db.all_brent()
        )
        return str(path)

    def import_spreadsheet(self, xls_path: str | None = None) -> dict[str, Any]:
        """Import historical prices recorded before OilWatch existed.

        The workbook is no longer the source of truth — the database is — so this
        is a one-off migration aid and the path must be given explicitly rather
        than defaulting to a drive letter that may not be mapped.
        """
        from oilwatch.import_xls import import_spreadsheet

        self.db.init_schema()
        if not xls_path:
            raise ValueError(
                "Pass the workbook path explicitly, for example: "
                "oilwatch import-spreadsheet --path \"P:\\Public Documents\\Oil Prices.xls\". "
                "The database is the source of truth; this import is historical only."
            )
        return import_spreadsheet(self.db, Path(xls_path), self.settings.quote_quantity_liters)

    def update_brent(self) -> dict[str, Any]:
        from oilwatch.brent import update_brent

        self.db.init_schema()
        return update_brent(self.db)

    def _resolve_supplier(self, supplier: str | int) -> dict[str, Any]:
        """Find a supplier by id, or by a fragment of its name or website.

        The owner says "I bought from Scottish Fuels", not "supplier id 1", so a
        name has to work. An ambiguous name is refused rather than guessed:
        filing a purchase against the wrong supplier would be worse than asking.
        """
        if isinstance(supplier, int) or str(supplier).strip().isdigit():
            found = self.db.get_supplier(int(supplier))
            if not found:
                raise ValueError(f"Unknown supplier id: {supplier}")
            return found

        needle = str(supplier).strip().lower()
        if not needle:
            raise ValueError("Name the supplier you bought from.")
        matches = [
            row
            for row in self.db.list_suppliers(include_inactive=True)
            if needle in (row.get("name") or "").lower() or needle in (row.get("website") or "").lower()
        ]
        if not matches:
            raise ValueError(f"No supplier matches {supplier!r}. Run `oilwatch suppliers` for the list.")
        if len(matches) > 1:
            options = ", ".join(f"{row['id']}: {row['name']}" for row in matches)
            raise ValueError(f"{supplier!r} matches several suppliers ({options}). Use the supplier id.")
        return matches[0]

    def record_purchase(
        self,
        supplier: str | int,
        *,
        quantity_liters: int | None = None,
        price_per_liter: float | None = None,
        total_price: float | None = None,
        code: str | None = None,
        reference: str | None = None,
        notes: str = "",
        ordered_at: str | None = None,
        status: str = "ordered",
    ) -> dict[str, Any]:
        """Write down a purchase the owner has already made.

        Nothing here drives a browser or calls a connector: the owner buys by
        phone or on a supplier's own site, and this only records what happened.
        Prices are GBP per litre inclusive of VAT, like every other price in the
        database, so a purchase can be compared with the quotes behind it.
        Either the per-litre price or the total paid is enough; the other is
        derived from the quantity.
        """
        self.db.init_schema()
        resolved = self._resolve_supplier(supplier)
        litres = quantity_liters or self.settings.quote_quantity_liters

        # Either figure is enough. Each derivation sits inside the branch that
        # proves the value it reads is not None — which the guard cannot say to a
        # type checker — and neither one overwrites a figure that was given.
        if price_per_liter is None:
            if total_price is None:
                raise ValueError("Give the price per litre or the total paid.")
            price_per_liter = round(total_price / litres, 4)
        elif total_price is None:
            total_price = round(price_per_liter * litres, 2)

        record = {
            "supplier_id": resolved["id"],
            "created_at": ordered_at or utcnow_naive().isoformat(),
            "quantity_liters": litres,
            "agreed_price_per_liter": price_per_liter,
            "status": status,
            "reference": reference,
            "notes": notes,
            "raw_payload": {
                "total_price": total_price,
                "discount_code": code,
                "supplier_name": resolved["name"],
            },
        }
        # Returned from the record rather than read back as "the newest row": a
        # purchase entered with a back-dated --date is not the newest row.
        return {
            "id": self.db.record_order(record),
            "supplier_id": resolved["id"],
            "supplier_name": resolved["name"],
            "website": resolved.get("website"),
            "created_at": record["created_at"],
            "quantity_liters": litres,
            "agreed_price_per_liter": price_per_liter,
            "total_price": total_price,
            "discount_code": code,
            "status": status,
            "reference": reference,
            "notes": notes,
        }

    def purchases(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Purchases already recorded, newest first, totals and codes unpacked."""
        self.db.init_schema()
        rows = self.db.list_orders(limit=limit)
        for row in rows:
            payload = json.loads(row.pop("raw_payload_json") or "{}")
            row["discount_code"] = payload.get("discount_code")
            row["total_price"] = payload.get("total_price")
            if row["total_price"] is None:
                # The payload need not carry a total: the automated ordering path
                # this repo used to have stored the connector's own payload, and
                # none of those carried one, so its rows read back as "total
                # unknown". The columns always hold what the order cost, so
                # derive it; a discount code it genuinely does not have.
                row["total_price"] = round(
                    row["agreed_price_per_liter"] * row["quantity_liters"], 2
                )
        return rows

