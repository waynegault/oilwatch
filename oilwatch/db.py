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
    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
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
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with closing(self.connect()) as conn, conn:
            conn.executescript(SCHEMA)

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
            "notes": record.get("notes", ""),
        }
        with closing(self.connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO suppliers (
                    name, website, status, query, title, snippet, email, phone, address,
                    latitude, longitude, distance_miles, connector_type, connector_config_json, notes
                ) VALUES (
                    :name, :website, :status, :query, :title, :snippet, :email, :phone, :address,
                    :latitude, :longitude, :distance_miles, :connector_type, :connector_config_json, :notes
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
                    total_price, currency, source, notes, raw_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    json.dumps(record.get("raw_payload", {})),
                ),
            )
            return int(cursor.lastrowid)

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
            return int(cursor.lastrowid)

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

    def latest_quotes(self, max_age_days: int | None = None) -> list[dict[str, Any]]:
        """Return the most recent successful quote for each supplier.

        ``max_age_days`` caps how old that quote is allowed to be; leave it unset
        to take the newest successful row whatever its age. Observed timestamps
        are all ``YYYY-MM-DD``-prefixed, so an ISO date cutoff compares correctly
        as a string against both the date-only and datetime rows.
        """
        cutoff_clause = ""
        params: tuple[Any, ...] = ()
        if max_age_days is not None:
            cutoff_clause = "AND observed_at >= ?"
            params = ((utcnow_naive() - timedelta(days=max_age_days)).date().isoformat(),)
        with closing(self.connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT q.*, s.name AS supplier_name, s.website
                FROM quotes q
                JOIN suppliers s ON s.id = q.supplier_id
                JOIN (
                    SELECT supplier_id, MAX(observed_at) AS max_observed_at
                    FROM quotes
                    WHERE status = 'ok' {cutoff_clause}
                    GROUP BY supplier_id
                ) latest
                ON latest.supplier_id = q.supplier_id AND latest.max_observed_at = q.observed_at
                ORDER BY q.price_per_liter ASC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

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
