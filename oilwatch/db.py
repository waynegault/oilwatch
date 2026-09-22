from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from typing import Any

from oilwatch.models import utcnow_naive

SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    website TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'manual_review',
    kind TEXT NOT NULL DEFAULT 'supplier',
    query TEXT,
    title TEXT,
    snippet TEXT,
    email TEXT,
    phone TEXT,
    address TEXT,
    latitude REAL,
    longitude REAL,
    distance_miles REAL,
    connector_type TEXT NOT NULL DEFAULT 'manual',
    connector_config_json TEXT NOT NULL DEFAULT '{}',
    notes TEXT,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    quantity_liters INTEGER NOT NULL,
    status TEXT NOT NULL,
    price_per_liter REAL,
    total_price REAL,
    currency TEXT NOT NULL,
    source TEXT NOT NULL,
    notes TEXT,
    reason TEXT,
    valid_until TEXT,
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
);

-- One row per price sweep: written when it starts, finished_at filled in when it
-- reports back. A row with no finished_at that is older than any sweep can take
-- is a run that died, and saying so beats leaving a caller to guess.
CREATE TABLE IF NOT EXISTS sweeps (
    started_at TEXT PRIMARY KEY,
    finished_at TEXT,
    started_by TEXT
);

-- One row per price actually asked for: written when the request goes out, and
-- closed when a quote for that supplier is recorded. It exists because the
-- channels that answer by hand - a form, an email - reply later, and nothing
-- else here can say whether that answer is still owed. `quotes` holds what came
-- back; a request that was never made and one still being thought about look
-- identical in an empty mailbox, and differ only here.
CREATE TABLE IF NOT EXISTS quote_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id INTEGER NOT NULL,
    requested_at TEXT NOT NULL,
    channel TEXT NOT NULL,
    quantity_liters INTEGER,
    postcode TEXT,
    note TEXT,
    answered_at TEXT,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
);

-- A detached sweep, run as its own process so it outlives the MCP session that
-- asked for it. `state` is running / finished / failed; progress counts and the
-- results land here as the worker goes, because the caller's next question is
-- "how far has it got?" and the worker may outlive the caller entirely.
CREATE TABLE IF NOT EXISTS refresh_jobs (
    job_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    started_by TEXT,
    state TEXT NOT NULL DEFAULT 'running',
    total INTEGER,
    done INTEGER NOT NULL DEFAULT 0,
    results_json TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    quantity_liters INTEGER NOT NULL,
    agreed_price_per_liter REAL NOT NULL,
    status TEXT NOT NULL,
    reference TEXT,
    notes TEXT,
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
);

CREATE TABLE IF NOT EXISTS brent_crude (
    observed_at TEXT PRIMARY KEY,
    price_usd_per_barrel REAL NOT NULL,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id INTEGER,
    code TEXT,
    amount_gbp REAL NOT NULL,
    min_litres INTEGER,
    max_litres INTEGER,
    expires_at TEXT,
    terms TEXT,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    raw_text TEXT,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
);

CREATE TABLE IF NOT EXISTS processed_messages (
    message_id TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL
);

-- One fuel-mail judgement per unrecognised sender. Keyed by domain rather than
-- by message id on purpose: unrecognised mail is left in the mailbox, so the same
-- messages are re-read every sweep, and a verdict held per message would ask
-- again for all of them, hourly, forever. It is also what keeps the alert firing
-- on later sweeps without spending a request to reach the same answer.
CREATE TABLE IF NOT EXISTS sender_judgements (
    domain TEXT PRIMARY KEY,
    fuel_probability REAL NOT NULL,
    judged_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _inserted_id(cursor: sqlite3.Cursor) -> int:
        """The row id of the row an INSERT just wrote.

        ``lastrowid`` is Optional in the sqlite3 typeshed; after an INSERT it is
        always set, so a missing one means the statement was not an insert.
        """
        row_id = cursor.lastrowid
        if row_id is None:  # pragma: no cover - an INSERT always sets it
            raise RuntimeError("the insert returned no row id")
        return row_id

    def init_schema(self) -> None:
        with closing(self.connect()) as conn, conn:
            conn.executescript(SCHEMA)
            # `valid_until` arrived after the first release, so add it in place:
            # an existing database keeps working instead of needing a reset.
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(quotes)")}
            if "valid_until" not in columns:
                conn.execute("ALTER TABLE quotes ADD COLUMN valid_until TEXT")
            if "reason" not in columns:
                conn.execute("ALTER TABLE quotes ADD COLUMN reason TEXT")
            # `kind` distinguishes a supplier from a benchmark (Fueltool is a
            # UK-average figure, not somewhere you can buy). Same additive
            # reasoning as `valid_until` above: an existing database keeps
            # working, and every row already there is a supplier.
            supplier_columns = {row["name"] for row in conn.execute("PRAGMA table_info(suppliers)")}
            if "kind" not in supplier_columns:
                conn.execute("ALTER TABLE suppliers ADD COLUMN kind TEXT NOT NULL DEFAULT 'supplier'")
            # Give pre-existing rows a validity too, so an older database compares
            # on the same footing as a fresh one instead of reporting null. Idempotent:
            # only rows where it is still unset are touched.
            conn.execute(
                "UPDATE quotes SET valid_until = "
                "strftime('%Y-%m-%dT%H:%M:%S', datetime(observed_at, '+24 hours')) "
                "WHERE valid_until IS NULL"
            )

    def upsert_supplier(self, record: dict[str, Any]) -> int:
        payload = {
            "name": record["name"],
            "website": record["website"],
            "status": record.get("status", "manual_review"),
            "query": record.get("query"),
            "title": record.get("title"),
            "snippet": record.get("snippet"),
            "email": record.get("email"),
            "phone": record.get("phone"),
            "address": record.get("address"),
            "latitude": record.get("latitude"),
            "longitude": record.get("longitude"),
            "distance_miles": record.get("distance_miles"),
            "connector_type": record.get("connector_type", "manual"),
            "connector_config_json": json.dumps(record.get("connector_config", {})),
            "kind": record.get("kind", "supplier"),
            "notes": record.get("notes", ""),
        }
        with closing(self.connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO suppliers (
                    name, website, status, query, title, snippet, email, phone, address,
                    latitude, longitude, distance_miles, connector_type, connector_config_json,
                    kind, notes
                ) VALUES (
                    :name, :website, :status, :query, :title, :snippet, :email, :phone, :address,
                    :latitude, :longitude, :distance_miles, :connector_type, :connector_config_json,
                    :kind, :notes
                )
                ON CONFLICT(website) DO UPDATE SET
                    name=excluded.name,
                    status=excluded.status,
                    query=excluded.query,
                    title=excluded.title,
                    snippet=excluded.snippet,
                    email=COALESCE(excluded.email, suppliers.email),
                    phone=COALESCE(excluded.phone, suppliers.phone),
                    address=COALESCE(excluded.address, suppliers.address),
                    latitude=COALESCE(excluded.latitude, suppliers.latitude),
                    longitude=COALESCE(excluded.longitude, suppliers.longitude),
                    distance_miles=COALESCE(excluded.distance_miles, suppliers.distance_miles),
                    connector_type=excluded.connector_type,
                    connector_config_json=excluded.connector_config_json,
                    kind=excluded.kind,
                    notes=excluded.notes,
                    last_seen_at=CURRENT_TIMESTAMP
                """,
                payload,
            )
            row = conn.execute(
                "SELECT id FROM suppliers WHERE website = ?",
                (record["website"],),
            ).fetchone()
            return int(row["id"])

    def record_quote(self, record: dict[str, Any]) -> int:
        with closing(self.connect()) as conn, conn:
            cursor = conn.execute(
                """
                INSERT INTO quotes (
                    supplier_id, observed_at, quantity_liters, status, price_per_liter,
                    total_price, currency, source, notes, reason, valid_until,
                    raw_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["supplier_id"],
                    record["observed_at"],
                    record["quantity_liters"],
                    record["status"],
                    record.get("price_per_liter"),
                    record.get("total_price"),
                    record["currency"],
                    record["source"],
                    record.get("notes", ""),
                    record.get("reason"),
                    record.get("valid_until"),
                    json.dumps(record.get("raw_payload", {})),
                ),
            )
            # A price answers whatever was asked of that supplier, so the request
            # stops counting as outstanding here rather than waiting for someone
            # to read the mailbox again. Only a price does it: an attempt that
            # raised, or one that came back with nothing, leaves the request owed.
            if record.get("status") == "ok":
                conn.execute(
                    "UPDATE quote_requests SET answered_at = ? "
                    "WHERE supplier_id = ? AND answered_at IS NULL",
                    (record["observed_at"], record["supplier_id"]),
                )
            return self._inserted_id(cursor)

    def quote_already_recorded(self, record: dict[str, Any]) -> bool:
        """True when an identical observation is already stored.

        Sweeping old mail is deliberate, so the same reply can reach the caller
        twice — most often because a message's id changes when it moves between
        folders, which slips past the processed-message ledger. The same
        supplier, timestamp to the second, price and source is one observation,
        not a second quote.
        """
        with closing(self.connect()) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM quotes
                WHERE supplier_id IS ? AND observed_at IS ? AND source IS ?
                  AND price_per_liter IS ?
                """,
                (
                    record["supplier_id"],
                    record["observed_at"],
                    record.get("source"),
                    record.get("price_per_liter"),
                ),
            ).fetchone()
        return row is not None

    def record_order(self, record: dict[str, Any]) -> int:
        with closing(self.connect()) as conn, conn:
            cursor = conn.execute(
                """
                INSERT INTO orders (
                    supplier_id, created_at, quantity_liters, agreed_price_per_liter,
                    status, reference, notes, raw_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["supplier_id"],
                    record["created_at"],
                    record["quantity_liters"],
                    record["agreed_price_per_liter"],
                    record["status"],
                    record.get("reference"),
                    record.get("notes", ""),
                    json.dumps(record.get("raw_payload", {})),
                ),
            )
            return self._inserted_id(cursor)

    def list_orders(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Recorded purchases, newest first, with the supplier's name.

        The orders table was write-only: nothing ever read it back, so "who did
        I buy from last time, and what did I pay" had no answer.
        """
        query = """
            SELECT o.*, s.name AS supplier_name, s.website
            FROM orders o
            LEFT JOIN suppliers s ON s.id = o.supplier_id
            ORDER BY o.created_at DESC
        """
        params: tuple[Any, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        with closing(self.connect()) as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def list_suppliers(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM suppliers"
        if not include_inactive:
            query += " WHERE status != 'inactive'"
        query += " ORDER BY distance_miles ASC, name ASC"
        with closing(self.connect()) as conn:
            rows = conn.execute(query).fetchall()
        return [self._supplier_row_to_dict(row) for row in rows]

    def get_supplier(self, supplier_id: int) -> dict[str, Any] | None:
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,)).fetchone()
        if not row:
            return None
        return self._supplier_row_to_dict(row)

    def latest_attempts(self) -> list[dict[str, Any]]:
        """The most recent attempt for every supplier, whatever it returned.

        Unlike :meth:`not_refreshed_quotes` this needs no successful quote to
        exist, so it answers "what did the last ask actually do?" for a supplier
        that has never given a price at all.
        """
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT s.name AS supplier_name, s.website, s.connector_config_json,
                       s.phone, s.email, s.kind,
                       attempt.observed_at, attempt.status, attempt.reason, attempt.notes
                FROM suppliers s
                JOIN (
                    SELECT supplier_id, MAX(observed_at) AS max_attempt
                    FROM quotes GROUP BY supplier_id
                ) a ON a.supplier_id = s.id
                JOIN quotes attempt
                  ON attempt.supplier_id = s.id AND attempt.observed_at = a.max_attempt
                WHERE s.status != 'inactive'
                ORDER BY s.name ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def start_sweep(self, started_at: str, started_by: str) -> None:
        """Mark a price sweep as started, so another caller can see it running."""
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO sweeps (started_at, finished_at, started_by) "
                "VALUES (?, NULL, ?)",
                (started_at, started_by),
            )

    def finish_sweep(self, started_at: str) -> None:
        """Mark the sweep that started at this instant as finished.

        Keyed on the start it recorded rather than on "the newest", so a sweep
        that overlapped another still clears its own row.
        """
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "UPDATE sweeps SET finished_at = ? WHERE started_at = ?",
                (utcnow_naive().isoformat(), started_at),
            )

    def latest_sweep(self) -> dict[str, Any] | None:
        """The newest sweep row, or None when no sweep has ever been recorded."""
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT started_at, finished_at, started_by FROM sweeps "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def record_quote_request(
        self,
        supplier_id: int,
        channel: str,
        *,
        requested_at: str | None = None,
        quantity_liters: int | None = None,
        postcode: str | None = None,
        note: str = "",
    ) -> int:
        """Record that a supplier has been asked for a price.

        ``channel`` is how it was asked - form or email - because that is
        what says where the answer will come from. The row stays open until a
        quote for the supplier is recorded (see :meth:`record_quote`), which is
        what makes "is a reply still owed?" answerable without reading the
        mailbox at all.
        """
        if requested_at is None:
            requested_at = utcnow_naive().isoformat()
        with closing(self.connect()) as conn, conn:
            cursor = conn.execute(
                "INSERT INTO quote_requests "
                "(supplier_id, requested_at, channel, quantity_liters, postcode, note) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (supplier_id, requested_at, channel, quantity_liters, postcode, note),
            )
            return self._inserted_id(cursor)

    def outstanding_quote_requests(self) -> list[dict[str, Any]]:
        """Requests that no recorded quote has answered, oldest first.

        The oldest first because a request that has been owed longest is the one
        worth chasing; the supplier's name is joined in rather than left to the
        caller, since the id alone answers nothing a reader can act on.
        """
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT r.id, r.supplier_id, s.name AS supplier_name, r.requested_at,
                       r.channel, r.quantity_liters, r.postcode, r.note
                FROM quote_requests r
                JOIN suppliers s ON s.id = r.supplier_id
                WHERE r.answered_at IS NULL
                ORDER BY r.requested_at
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def create_refresh_job(
        self, job_id: str, started_at: str, started_by: str, total: int
    ) -> None:
        """Record a detached sweep before its process is spawned.

        Created by the caller rather than the worker, so the id it returns is
        already readable: a client that asks for the status immediately gets
        "running, 0 of 17" rather than "no such job".
        """
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO refresh_jobs (job_id, started_at, started_by, state, total, done) "
                "VALUES (?, ?, ?, 'running', ?, 0)",
                (job_id, started_at, started_by, total),
            )

    def progress_refresh_job(self, job_id: str, done: int) -> None:
        """Say how many suppliers have come back so far."""
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE refresh_jobs SET done = ? WHERE job_id = ?", (done, job_id))

    def finish_refresh_job(
        self,
        job_id: str,
        state: str,
        results: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> None:
        """Close a job out as ``finished`` or ``failed``, with what it produced."""
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "UPDATE refresh_jobs SET finished_at = ?, state = ?, results_json = ?, error = ? "
                "WHERE job_id = ?",
                (
                    utcnow_naive().isoformat(),
                    state,
                    json.dumps(results) if results is not None else None,
                    error,
                    job_id,
                ),
            )

    def refresh_job(self, job_id: str | None = None) -> dict[str, Any] | None:
        """A job by id, or the newest one when no id is given."""
        query = "SELECT * FROM refresh_jobs"
        params: tuple[Any, ...] = ()
        if job_id:
            query += " WHERE job_id = ?"
            params = (job_id,)
        else:
            query += " ORDER BY started_at DESC LIMIT 1"
        with closing(self.connect()) as conn:
            row = conn.execute(query, params).fetchone()
        if not row:
            return None
        job = dict(row)
        raw = job.pop("results_json", None)
        job["results"] = json.loads(raw) if raw else None
        return job

    def newest_observation(self) -> str | None:
        """The newest ``observed_at`` in the quotes table, or None when empty.

        Ages a *refresh* rather than a price: it is the most recent attempt of
        any kind, so it answers "when did we last go and look?".
        """
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT MAX(observed_at) AS newest FROM quotes").fetchone()
        return row["newest"] if row else None

    def latest_quotes(self, max_age_days: int | None = None) -> list[dict[str, Any]]:
        """Return the most recent successful quote for each supplier.

        ``max_age_days`` caps how old that quote is allowed to be; leave it unset
        to take the newest successful row whatever its age. The cutoff is a full
        timestamp, so one day means 24 hours: comparing dates instead kept
        anything from the previous calendar day, which let a quote up to ~48 hours
        old — including ones whose own ``valid_until`` had already passed — be
        offered as current. Observed timestamps are all ``YYYY-MM-DD``-prefixed,
        so the string comparison still holds against date-only rows (which now
        count only while they are today's).
        """
        # The window is optional, but the SQL stays a single static string and
        # the cutoff is always bound: COALESCE(NULL, observed_at) makes the
        # comparison a no-op when no window is asked for. Interpolating the
        # clause instead (as this once did) is the shape that invites injection
        # the moment a caller-supplied value reaches it.
        cutoff = (
            None
            if max_age_days is None
            else (utcnow_naive() - timedelta(days=max_age_days)).isoformat()
        )
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT q.*, s.name AS supplier_name, s.website, s.connector_config_json,
                       s.phone, s.email, s.kind
                FROM quotes q
                JOIN suppliers s ON s.id = q.supplier_id
                JOIN (
                    SELECT supplier_id, MAX(observed_at) AS max_observed_at
                    FROM quotes
                    WHERE status = 'ok'
                      AND observed_at >= COALESCE(?, observed_at)
                    GROUP BY supplier_id
                ) latest
                ON latest.supplier_id = q.supplier_id AND latest.max_observed_at = q.observed_at
                ORDER BY q.price_per_liter ASC
                """,
                (cutoff,),
            ).fetchall()
        return [dict(row) for row in rows]

    def stale_quotes(self, max_age_days: int | None = None) -> list[dict[str, Any]]:
        """The newest successful quote for each supplier *outside* the window.

        The mirror of :meth:`latest_quotes`: it names the suppliers a recency
        window silently drops, with the last price each did give. Unbounded
        (``max_age_days`` unset) there is nothing being excluded, so it returns
        an empty list rather than every row.
        """
        if max_age_days is None:
            return []
        cutoff = (utcnow_naive() - timedelta(days=max_age_days)).isoformat()
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT q.*, s.name AS supplier_name, s.website
                FROM quotes q
                JOIN suppliers s ON s.id = q.supplier_id
                JOIN (
                    SELECT supplier_id, MAX(observed_at) AS max_observed_at
                    FROM quotes
                    WHERE status = 'ok'
                    GROUP BY supplier_id
                    HAVING MAX(observed_at) < ?
                ) latest
                ON latest.supplier_id = q.supplier_id AND latest.max_observed_at = q.observed_at
                ORDER BY q.observed_at ASC
                """,
                (cutoff,),
            ).fetchall()
        return [dict(row) for row in rows]

    def unquoted_suppliers(self) -> list[dict[str, Any]]:
        """Suppliers with no quote row at all, inactive ones excluded.

        Distinct from :meth:`stale_quotes`, which names suppliers whose quotes
        are all outside the window. This is "no price has ever been recorded from
        them", which reads identically in an empty market but is a different
        problem: one needs a refresh, the other may need a connector.
        """
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT s.id AS supplier_id, s.name AS supplier_name, s.website,
                       s.connector_type, s.connector_config_json, s.phone, s.email, s.kind
                FROM suppliers s
                LEFT JOIN quotes q ON q.supplier_id = s.id
                WHERE s.status != 'inactive' AND q.id IS NULL
                ORDER BY s.name ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def not_refreshed_quotes(self, max_age_days: int | None = None) -> list[dict[str, Any]]:
        """Newest successful quote for each supplier whose *latest* try failed.

        The mirror of :meth:`stale_quotes` for the other way a figure can be
        older than it reads: a supplier that gave a price earlier but whose most
        recent attempt returned none — a quote portal session that expired, a
        site that timed out. The stored price is still inside the window, so it
        keeps being compared as if it were current; this names it so a report can
        say "last quoted at 09:16, not refreshed since" rather than presenting
        the earlier figure as the run's own result.

        Returns ``[]`` when no window is set: unbounded, every row is in play and
        the distinction stops being meaningful.
        """
        if max_age_days is None:
            return []
        cutoff = (utcnow_naive() - timedelta(days=max_age_days)).isoformat()
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT s.name AS supplier_name, s.website,
                       ok.observed_at AS observed_at, ok.price_per_liter,
                       attempt.observed_at AS last_attempt_at,
                       attempt.status AS last_attempt_status,
                       attempt.reason AS last_attempt_reason,
                       attempt.notes AS last_attempt_note
                FROM suppliers s
                JOIN (
                    SELECT supplier_id, MAX(observed_at) AS max_attempt
                    FROM quotes GROUP BY supplier_id
                ) a ON a.supplier_id = s.id
                JOIN quotes attempt
                  ON attempt.supplier_id = s.id AND attempt.observed_at = a.max_attempt
                JOIN (
                    SELECT supplier_id, MAX(observed_at) AS max_ok
                    FROM quotes WHERE status = 'ok' GROUP BY supplier_id
                ) o ON o.supplier_id = s.id
                JOIN quotes ok
                  ON ok.supplier_id = s.id AND ok.observed_at = o.max_ok
                WHERE attempt.status <> 'ok'
                  AND ok.observed_at >= ?
                ORDER BY ok.observed_at ASC
                """,
                (cutoff,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_discount(self, record: dict[str, Any]) -> int:
        """Store one discount offer captured from a supplier email.

        An exact repeat is ignored rather than stored again: the same message can
        be mined twice (its id changes when it moves to another folder, which
        slips past the processed-message ledger) and duplicate rows only add
        noise to every comparison.
        """
        with closing(self.connect()) as conn, conn:
            existing = conn.execute(
                """
                SELECT id FROM discounts
                WHERE supplier_id IS ? AND code IS ? AND amount_gbp = ?
                  AND min_litres IS ? AND max_litres IS ?
                """,
                (
                    record.get("supplier_id"),
                    record.get("code"),
                    record["amount_gbp"],
                    record.get("min_litres"),
                    record.get("max_litres"),
                ),
            ).fetchone()
            if existing is not None:
                return int(existing["id"])
            cursor = conn.execute(
                """
                INSERT INTO discounts (
                    supplier_id, code, amount_gbp, min_litres, max_litres,
                    expires_at, terms, source, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("supplier_id"),
                    record.get("code"),
                    record["amount_gbp"],
                    record.get("min_litres"),
                    record.get("max_litres"),
                    record.get("expires_at"),
                    record.get("terms", ""),
                    record.get("source", "email"),
                    record.get("observed_at", utcnow_naive().isoformat()),
                ),
            )
            return self._inserted_id(cursor)

    def active_discounts(self) -> list[dict[str, Any]]:
        """Discount offers that have not expired, largest first.

        Offers with no stated expiry are kept: "no expiry given" is not the same
        as "expired".
        """
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM discounts
                WHERE expires_at IS NULL OR expires_at > ?
                ORDER BY amount_gbp DESC
                """,
                (utcnow_naive().isoformat(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def message_processed(self, message_id: str) -> bool:
        """True when this message has already been mined for prices or codes."""
        if not message_id:
            return False
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM processed_messages WHERE message_id = ?", (message_id,)
            ).fetchone()
        return row is not None

    def mark_message_processed(self, message_id: str) -> None:
        """Record a message as handled.

        Needed now that the sweep looks at mail that is already read or deleted:
        without it, a second pass would record the same quote again.
        """
        if not message_id:
            return
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT OR IGNORE INTO processed_messages (message_id, processed_at) VALUES (?, ?)",
                (message_id, utcnow_naive().isoformat()),
            )

    def sender_judgement(self, domain: str) -> float | None:
        """The stored fuel-mail probability for a sender domain, if judged before.

        ``None`` means this domain has not been judged, which is the caller's cue
        to ask rather than a verdict of its own.
        """
        if not domain:
            return None
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT fuel_probability FROM sender_judgements WHERE domain = ?",
                (domain,),
            ).fetchone()
        return None if row is None else float(row["fuel_probability"])

    def record_sender_judgement(self, domain: str, fuel_probability: float) -> None:
        """Store a sender's fuel-mail probability so it is asked only once."""
        if not domain:
            return
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO sender_judgements "
                "(domain, fuel_probability, judged_at) VALUES (?, ?, ?)",
                (domain, float(fuel_probability), utcnow_naive().isoformat()),
            )

    def all_quotes(self) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT q.*, s.name AS supplier_name
                FROM quotes q
                JOIN suppliers s ON s.id = q.supplier_id
                ORDER BY q.observed_at ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def record_brent(self, observed_at: str, price_usd_per_barrel: float, source: str) -> bool:
        """Insert a Brent crude point if not already present. Returns True if inserted."""
        with closing(self.connect()) as conn, conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO brent_crude (observed_at, price_usd_per_barrel, source) VALUES (?, ?, ?)",
                (observed_at, price_usd_per_barrel, source),
            )
            return cursor.rowcount > 0

    def all_brent(self) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                "SELECT observed_at, price_usd_per_barrel, source FROM brent_crude ORDER BY observed_at ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def record_brent_many(self, rows: list[tuple[str, float, str]]) -> int:
        """Bulk-insert ``(observed_at, price_usd_per_barrel, source)`` rows.

        Duplicate dates are ignored. Returns the number of rows actually inserted.
        """
        if not rows:
            return 0
        with closing(self.connect()) as conn, conn:
            before = conn.total_changes
            conn.executemany(
                "INSERT OR IGNORE INTO brent_crude (observed_at, price_usd_per_barrel, source) VALUES (?, ?, ?)",
                rows,
            )
            return conn.total_changes - before

    def mark_missing_suppliers_inactive(self, active_websites: list[str]) -> None:
        with closing(self.connect()) as conn, conn:
            if not active_websites:
                conn.execute("UPDATE suppliers SET status = 'inactive'")
                return
            placeholders = ", ".join("?" for _ in active_websites)
            conn.execute(
                f"UPDATE suppliers SET status = 'inactive' WHERE website NOT IN ({placeholders})",
                active_websites,
            )

    @staticmethod
    def _supplier_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["connector_config"] = json.loads(data.pop("connector_config_json") or "{}")
        return data
