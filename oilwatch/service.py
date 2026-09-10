from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from oilwatch.analytics import AnalyticsService
from oilwatch.config import Settings, load_settings, load_supplier_overrides
from oilwatch.db import Database
from oilwatch.discovery import DiscoveryService
from oilwatch.geo import GeoService
from oilwatch.logging_setup import get_logger
from oilwatch.models import utcnow_naive
from oilwatch.ordering import OrderService
from oilwatch.quotes import QuoteService

log = get_logger("service")


class OilWatchApp:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd()
        self.settings: Settings = load_settings(self.root)
        self.db = Database(self.settings.database_path)
        self.geo = GeoService()
        self.discovery = DiscoveryService(self.settings, self.geo)
        self.quotes = QuoteService(self.settings.currency, self.settings.home.label)
        self.analytics = AnalyticsService()
        self.ordering = OrderService()

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
    ) -> list[dict[str, Any]]:
        self.db.init_schema()
        results: list[dict[str, Any]] = []
        for supplier in self.db.list_suppliers(include_inactive=False):
            try:
                result = self.quotes.quote_supplier(
                    supplier,
                    self.settings.quote_quantity_liters,
                    postcode=postcode,
                    prefer_browser=prefer_browser,
                )
                payload = result.to_record()
            except Exception as exc:  # noqa: BLE001
                log.warning("Quote collection failed for %s: %s", supplier["name"], exc)
                payload = {
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
                    "raw_payload": {},
                }
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
        return self.analytics.latest_market_snapshot(self._with_effective_prices(self._current_quotes()))

    def current_prices(self) -> list[dict[str, Any]]:
        self.db.init_schema()
        return self._with_effective_prices(self._current_quotes())

    def status(self) -> dict[str, Any]:
        self.db.init_schema()
        snapshot = self.analytics.latest_market_snapshot(self._with_effective_prices(self._current_quotes()))
        trend = self.analytics.price_trend(self.db.all_quotes())
        return {
            "market_snapshot": snapshot,
            "trend": trend,
            "recommendation": self.analytics.recommendation(snapshot, trend),
            # Carried here so "have I already ordered, and what did I pay?" is
            # answerable without a second call.
            "last_purchase": next(iter(self.purchases(limit=1)), None),
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

        if price_per_liter is None and total_price is None:
            raise ValueError("Give the price per litre or the total paid.")
        if price_per_liter is None:
            price_per_liter = round(float(total_price) / litres, 4)
        if total_price is None:
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
            row["total_price"] = payload.get("total_price")
            row["discount_code"] = payload.get("discount_code")
        return rows

    def place_order(
        self,
        supplier_id: int,
        agreed_price_per_liter: float,
        postcode: str | None = None,
        quantity_liters: int | None = None,
    ) -> dict[str, Any]:
        self.db.init_schema()
        supplier = self.db.get_supplier(supplier_id)
        if not supplier:
            raise ValueError(f"Unknown supplier id: {supplier_id}")
        quantity = quantity_liters or self.settings.quote_quantity_liters
        result = self.ordering.place_order(
            supplier=supplier,
            quantity_liters=quantity,
            agreed_price_per_liter=agreed_price_per_liter,
            postcode=postcode,
            home_label=self.settings.home.label,
        )
        self.db.record_order(result.to_record())
        return result.to_record()
