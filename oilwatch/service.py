from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from oilwatch.analytics import AnalyticsService
from oilwatch.config import (
    CHECKOUT_ROOT,
    Settings,
    load_settings,
    load_supplier_registry,
)
from oilwatch.db import Database
from oilwatch.discovery import DiscoveryService
from oilwatch.geo import GeoService
from oilwatch.logging_setup import get_logger
from oilwatch.models import utcnow_naive
from oilwatch.quotes import QuoteService

log = get_logger("service")

#: How long a sweep marker stands before it is read as a run that died rather
#: than one in progress. A sweep takes 1-3 minutes; ten is comfortably past any
#: of them, and matches the tool's own cooldown, so a marker that outlives this
#: is a crash rather than a slow supplier.
SWEEP_STALE_AFTER_MINUTES = 10


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
        for supplier in load_supplier_registry(self.root)["suppliers"]:
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
        started_by: str = "cli",
        job_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Quote every active supplier, with the sweep marked while it runs.

        The marker is written before the first browser opens and cleared however
        the sweep ends, including on a raise. A caller whose own request timed
        out reads it to tell "still running" from "died", which nothing else on
        record can answer: quote rows appear only as each supplier finishes, so
        a sweep thirty seconds in has left no trace at all yet.

        Pass ``job_id`` to run this as a recorded job — the detached worker does,
        and it is what makes progress and results readable after the caller that
        asked for the sweep is gone.
        """
        sweep_started_at = utcnow_naive().isoformat()
        self.db.start_sweep(sweep_started_at, started_by)
        try:
            return self._quote_every_supplier(
                postcode, prefer_browser, max_workers, job_id=job_id
            )
        finally:
            self.db.finish_sweep(sweep_started_at)

    def _quote_every_supplier(
        self,
        postcode: str | None,
        prefer_browser: bool,
        max_workers: int | None,
        job_id: str | None = None,
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
            # already work around. ``as_completed`` rather than ``map``: it
            # reports each supplier as it lands instead of making the caller wait
            # for the slowest, which is what lets a job show progress.
            with ThreadPoolExecutor(max_workers=min(workers, len(suppliers))) as pool:
                futures = {
                    pool.submit(quote_one, supplier): index
                    for index, supplier in enumerate(suppliers)
                }
                by_index: dict[int, dict[str, Any]] = {}
                for future in as_completed(futures):
                    by_index[futures[future]] = future.result()
                    if job_id is not None:
                        self.db.progress_refresh_job(job_id, len(by_index))
                # Rebuilt in supplier order rather than left as they landed:
                # ``as_completed`` is for reporting progress, but the result list
                # is a contract callers read in the order they asked for.
                payloads = [by_index[index] for index in range(len(suppliers))]

        results: list[dict[str, Any]] = []
        for supplier, payload in zip(suppliers, payloads, strict=True):
            # Recorded here, in the one thread, so concurrent quotes cannot
            # contend for the single SQLite file.
            self.db.record_quote(payload)

            # Errors are worth a warning; the routine manual_action_required
            # results stay at debug so a normal run does not produce a wall of
            # lines for suppliers that have no price to give.
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

            # The supplier's own facts, from its register record: what it is, the
            # page a human would order from when the config names one, and the
            # derived channel and contact. The raw JSON is dropped rather than
            # carried, so it never reaches a consumer.
            enriched.update(self.supplier_facts(row))
            enriched.pop("connector_config_json", None)

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

    @staticmethod
    def supplier_facts(row: dict[str, Any]) -> dict[str, Any]:
        """What a supplier is, and how to act on it, as fields.

        Shared by the priced rows and by the suppliers the last ask could not
        price — and it matters most for the second: the suppliers that quote only
        on request have no current price, so a reason with no ordering link or
        phone number beside it is half an answer.

        ``order_page`` is not taken from the quote's payload, because that URL is
        whatever the connector fetches and for some suppliers it is an API
        endpoint (Highland Fuels' getoffers.php) or a marketing page, so
        promoting it would hand a reader a link they cannot order from. An absent
        value means "not recorded", not "no page exists".
        """
        config = json.loads(row.get("connector_config_json") or "{}")
        order_page = config.get("order_page")
        kind = row.get("kind") or "supplier"
        phone = row.get("phone")
        email = row.get("email")
        return {
            "kind": kind,
            "order_page": order_page,
            "order_channel": OilWatchApp.order_channel(kind, order_page, email),
            "contact": {
                "phone": phone,
                "email": email,
                # The one URL to act on: the ordering page when one is recorded,
                # otherwise the supplier's site, which may be a marketing page.
                "url": order_page or row.get("website"),
            },
        }

    @staticmethod
    def order_channel(
        kind: str,
        order_page: str | None,
        email: str | None,
    ) -> str:
        """How this supplier is actually ordered from, as one value.

        The point is that a rule an agent must not get wrong becomes a field it
        can branch on, instead of prose it has to read and remember:

        - ``benchmark`` — not orderable at all. Fueltool is a UK-average figure,
          and presenting it as the winner is the mistake this prevents.
        - ``web`` — a human-orderable page is recorded (``order_page``). A
          supplier's ``website`` is deliberately not enough to earn this: for
          ValueOils it is a marketing page and for Highland Fuels an API
          endpoint.
        - ``email`` — no page, and an address on record to ask.
        - ``none`` — nothing usable recorded: no page and no address. A phone
          number alone lands here, because the app never rings a supplier, and
          that means *unrecorded*, not "cannot be ordered from": the honest gap,
          the same way an absent ``order_page`` reads.
        """
        if kind == "benchmark":
            return "benchmark"
        if order_page:
            return "web"
        if email:
            return "email"
        return "none"

    def cheapest(self) -> dict[str, Any]:
        self.db.init_schema()
        return self.analytics.latest_market_snapshot(
            self._with_effective_prices(self._current_quotes()),
            excluded_suppliers=self._excluded_suppliers(),
            not_refreshed_suppliers=self._not_refreshed_suppliers(),
            window_days=self.settings.max_quote_age_days,
        )

    def _never_quoted(self) -> list[dict[str, Any]]:
        """Suppliers with no quote row at all, with the same facts beside them.

        Nothing has ever been recorded from these, so the only useful thing on the
        row is how to go about asking — which is the same set of fields a priced
        row carries, for the same reason. The keys the database layer already
        returned are kept as they were: renaming a field a consumer may read is a
        separate decision from adding one.
        """
        entries: list[dict[str, Any]] = []
        for row in self.db.unquoted_suppliers():
            entry: dict[str, Any] = {
                "supplier_name": row["supplier_name"],
                "website": row["website"],
                "connector_type": row["connector_type"],
            }
            entry.update(self.supplier_facts(row))
            entries.append(entry)
        return entries

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
                # `supplier_name` rather than `name`, matching the priced rows and
                # `never_quoted`: the envelope named the supplier two ways, and a
                # consumer reading `.name` off one bucket and `.supplier_name` off
                # the next is a trap that needs no purpose.
                "supplier_name": row["supplier_name"],
                "website": row["website"],
                "last_attempt_at": row["observed_at"],
                "reason": row["reason"],
                "note": row["notes"],
            }
            # The same facts a priced row carries, and most needed here: these are
            # the suppliers a reader has to *ask*, so "quote by request" without
            # the page to request it on is the least actionable row in the output.
            entry.update(self.supplier_facts(row))
            if row["status"] == "error":
                failed.append(entry)
            elif row["status"] != "ok":
                no_quote.append(entry)
        return no_quote, failed

    def sweep_state(self) -> dict[str, Any]:
        """Whether a price sweep is running, and how the last one ended.

        This answers the one question a caller cannot answer for itself after its
        own request timed out: still running, or dead? A marker is written when a
        sweep starts and cleared when it finishes, so a marker still standing
        after longer than any sweep can take is not "in progress" — it is a run
        that never reported back, and saying so is the honest answer rather than
        reporting the last price as if it were being refreshed.
        """
        row = self.db.latest_sweep()
        state: dict[str, Any] = {
            "in_progress": False,
            "stale": False,
            "started_at": None,
            "started_by": None,
            "seconds_ago": None,
            "finished_at": None,
        }
        if row is None:
            return state

        # Clamped at zero: a marker can appear to be in the future after the
        # clock moves (a time correction, or the hour a DST change repeats), and
        # "started -3700 seconds ago" is worse than saying "just now".
        age = max(0.0, (utcnow_naive() - datetime.fromisoformat(row["started_at"])).total_seconds())
        finished = row["finished_at"]
        stale = finished is None and age > SWEEP_STALE_AFTER_MINUTES * 60
        return {
            "in_progress": finished is None and not stale,
            "stale": stale,
            "started_at": row["started_at"],
            "started_by": row["started_by"],
            "seconds_ago": int(age),
            "finished_at": finished,
        }

    def start_background_sweep(
        self, started_by: str = "mcp", postcode: str | None = None
    ) -> dict[str, Any]:
        """Start a sweep as its own process and return its job id immediately.

        A detached process, not a thread or a task: the MCP server is spawned per
        session over stdio, so anything living inside it dies with the session —
        and the whole reason to ask for a background sweep is that the session is
        likely to end, or to time out, before the sweep is done. The job row is
        written first, so the id returned here is readable at once and the client
        gets "running, 0 of 17" rather than "no such job".
        """
        self.db.init_schema()
        job_id = uuid.uuid4().hex
        started_at = utcnow_naive().isoformat()
        total = len(self.db.list_suppliers())
        self.db.create_refresh_job(job_id, started_at, started_by, total)

        command = [
            sys.executable,
            "-m",
            "oilwatch.cli",
            "quote-all",
            "--browser",
            "--job-id",
            job_id,
        ]
        if postcode:
            command += ["--postcode", postcode]
        spawn: dict[str, Any] = {
            # The checkout as cwd, so the worker reads the same config and the
            # same database as the process that launched it.
            "cwd": str(self.root),
            "stdin": subprocess.DEVNULL,
            # Output is discarded rather than inherited: the parent's stdout may
            # be an MCP transport, and a stray line on it breaks the protocol.
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            # Its own process group and no console, so the client going away
            # cannot take the sweep with it.
            spawn["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            spawn["start_new_session"] = True
        subprocess.Popen(command, **spawn)

        return {
            "job_id": job_id,
            "state": "running",
            "started_at": started_at,
            "started_by": started_by,
            "total": total,
        }

    def run_refresh_job(self, job_id: str, postcode: str | None = None) -> list[dict[str, Any]]:
        """Run a recorded job to completion, closing the job either way.

        The exception is re-raised rather than swallowed: the worker's exit code
        is what a person debugging it by hand reads, and the job row already
        records the failure for the caller that asked for it.
        """
        try:
            results = self.quote_all(
                postcode=postcode,
                prefer_browser=True,
                started_by="job",
                job_id=job_id,
            )
        except Exception as exc:  # noqa: BLE001 - recorded on the job, then re-raised
            self.db.finish_refresh_job(job_id, "failed", error=f"{type(exc).__name__}: {exc}")
            raise
        self.db.finish_refresh_job(job_id, "finished", results=results)
        return results

    def refresh_job_status(self, job_id: str | None = None) -> dict[str, Any]:
        """What a sweep job is doing now, or what it did.

        ``stale`` is the honest answer for a job whose worker is gone: it still
        says running, but nothing has updated it for longer than any sweep takes,
        so waiting on it is waiting for a process that is not there.
        """
        job = self.db.refresh_job(job_id)
        if job is None:
            return {"found": False, "job_id": job_id}
        age = max(
            0.0, (utcnow_naive() - datetime.fromisoformat(job["started_at"])).total_seconds()
        )
        return {
            "found": True,
            "job_id": job["job_id"],
            "state": job["state"],
            "stale": job["state"] == "running" and age > SWEEP_STALE_AFTER_MINUTES * 60,
            "started_at": job["started_at"],
            "started_by": job["started_by"],
            "finished_at": job["finished_at"],
            "seconds_ago": int(age),
            "total": job["total"],
            "done": job["done"],
            "error": job["error"],
            "results": job["results"],
        }

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
            "never_quoted": self._never_quoted(),
            # Whether a sweep is running right now, so a caller whose own request
            # timed out can tell "still going" from "died" without starting
            # another one to find out.
            "refresh": self.sweep_state(),
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
            # And what is still owed an answer. A request made by form, email or
            # phone comes back later, from a person; a supplier thinking looks
            # exactly like a supplier nobody asked unless the ask is written
            # down, which is the whole reason this list exists.
            "awaiting_reply": self.db.outstanding_quote_requests(),
            # The same marker as `current_prices` carries: `status` is the other
            # call an agent makes when it wants to know what is going on.
            "refresh": self.sweep_state(),
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

