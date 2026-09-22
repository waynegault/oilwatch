"""Build the versioned price-history snapshot from the live database.

The live database is not committed: this repo is public and
``data/oilwatch.sqlite`` holds the owner's correspondence, his mailbox's message
ids and his delivery postcode. What is worth versioning is the other half — the
prices, the suppliers and the record of who was asked — so this writes that into
``data/oilwatch-history.sqlite``.

    python tools/build_snapshot.py [--check]

``--check`` prints what would be written and stops; nothing is written.

**Built, not copied.** SQLite leaves a deleted row in the file's free pages, so a
copy that dropped a table could still carry that table's contents. Every kept
table is written into a new database instead, which cannot inherit a page from a
table that was never copied.

**Withheld, and verified withheld.** Tables that identify the owner or his mail
are not copied at all (``sender_judgements``, ``processed_messages``, ``orders``,
``sweeps``, ``refresh_jobs``), values that identify him are nulled on the rows
that are kept (``discounts.code``, ``quote_requests.postcode``), and his own
details — read from ``oilwatch.identity`` rather than hardcoded, so this cannot
drift from what the connectors use — are replaced with ``<redacted-…>`` wherever
they survive inside a kept note. The build then checks its own output: it scans
every text column *and* the file's bytes for each of those details and for
samples drawn from the tables it dropped, and fails rather than writing a
snapshot that still carries one.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oilwatch.config import CHECKOUT_ROOT, load_settings  # noqa: E402
from oilwatch.identity import load_contact  # noqa: E402
from oilwatch.models import utcnow_naive  # noqa: E402

SOURCE = CHECKOUT_ROOT / "data" / "oilwatch.sqlite"
TARGET = CHECKOUT_ROOT / "data" / "oilwatch-history.sqlite"

#: Table -> the columns carried across. The schema is copied verbatim, so the
#: snapshot answers the same queries as the live database; a column left out is
#: NULL rather than absent, which is the honest shape for a withheld value.
KEPT_COLUMNS: dict[str, list[str]] = {
    "suppliers": [
        "id",
        "name",
        "website",
        "status",
        "kind",
        "connector_type",
        "email",
        "phone",
        "address",
        "notes",
        "first_seen_at",
        "last_seen_at",
    ],
    "quotes": [
        "id",
        "supplier_id",
        "observed_at",
        "quantity_liters",
        "status",
        "price_per_liter",
        "total_price",
        "currency",
        "source",
        "notes",
        "valid_until",
        "reason",
    ],
    "brent_crude": ["observed_at", "price_usd_per_barrel", "source"],
    "discounts": [
        "id",
        "supplier_id",
        "amount_gbp",
        "min_litres",
        "max_litres",
        "expires_at",
        "terms",
        "source",
        "observed_at",
    ],
    "quote_requests": [
        "id",
        "supplier_id",
        "requested_at",
        "channel",
        "quantity_liters",
        "note",
        "answered_at",
    ],
}

#: Not copied at all, and why - written into the snapshot so a reader can see
#: what it is missing rather than inferring it from absent rows.
WITHHELD_TABLES = {
    "sender_judgements": "the owner's correspondents (a sender domain per row)",
    "processed_messages": "mailbox message ids",
    "orders": "the owner's own purchase",
    "sweeps": "sweep bookkeeping",
    "refresh_jobs": "refresh bookkeeping",
}

#: Values nulled on rows that are kept, rather than the whole table dropped.
WITHHELD_VALUES = {
    ("discounts", "code"): "a code offered to the owner",
    ("quote_requests", "postcode"): "the delivery postcode",
    ("discounts", "raw_text"): "the offer text the code was read from",
    ("quotes", "raw_payload_json"): "connector payloads, which echo the enquiry",
    ("suppliers", "connector_config_json"): "connector config (the register has it)",
    ("suppliers", "latitude"): "supplier location, used only for distance",
    ("suppliers", "longitude"): "supplier location, used only for distance",
    ("suppliers", "distance_miles"): "distance from the owner's address",
    ("suppliers", "query"): "the discovery query that found it",
    ("suppliers", "title"): "the search result's title",
    ("suppliers", "snippet"): "the search result's snippet",
}

#: Every form a password has been written in, so one that survived an earlier
#: build cannot ride along in a note. A value that is already a placeholder is
#: left exactly as it stands: the 2026-09-15 scrub wrote "<redacted - see
#: config/supplier_credentials.json>", and rewriting it would only mangle a
#: sentence that is already safe.
PASSWORD = re.compile(r"(?i)\b(password|passwd|pwd)\b\s*[:=]\s*(?!<redact)\S+")


def redactions(contact: object, home_label: str) -> list[tuple[str, str]]:
    """The literal strings to replace, longest first, and what to put there.

    Longest first so a postcode inside an address is not half-replaced before the
    whole address is tried. Only non-empty values are listed: replacing "" would
    rewrite every string in the snapshot.
    """
    wanted = [
        (getattr(contact, "name", ""), "<redacted-name>"),
        (getattr(contact, "email", ""), "<redacted-email>"),
        (getattr(contact, "phone", ""), "<redacted-phone>"),
        (getattr(contact, "postcode", ""), "<redacted-postcode>"),
        (home_label, "<redacted-address>"),
    ]
    return sorted(
        ((value.strip(), replacement) for value, replacement in wanted if value.strip()),
        key=lambda pair: -len(pair[0]),
    )


def scrub(value: object, replacements: list[tuple[str, str]]) -> object:
    """Apply the replacements to one value, leaving non-text alone."""
    if not isinstance(value, str):
        return value
    for needle, replacement in replacements:
        if needle in value:
            value = value.replace(needle, replacement)
    return PASSWORD.sub(r"\1: <redacted>", value)


def build(source: Path, target: Path) -> dict[str, int]:
    """Write the snapshot, then prove it carries none of the withheld values."""
    if not source.exists():
        raise SystemExit(f"no live database at {source}")
    if target.exists():
        target.unlink()  # a rebuild must not inherit the last one's pages

    contact = load_contact()
    settings = load_settings()
    replacements = redactions(contact, settings.home.label)

    live = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    live.row_factory = sqlite3.Row
    written: dict[str, int] = {}

    snapshot = sqlite3.connect(target)
    snapshot.row_factory = sqlite3.Row
    try:
        with snapshot:
            for table, columns in KEPT_COLUMNS.items():
                ddl = live.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if ddl is None:
                    raise SystemExit(f"{table} is not in {source}; nothing to build from")
                snapshot.execute(ddl["sql"])
                rows = live.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
                placeholders = ", ".join("?" * len(columns))
                snapshot.executemany(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                    [
                        tuple(scrub(row[column], replacements) for column in columns)
                        for row in rows
                    ],
                )
                written[table] = len(rows)
                # Indexes make the snapshot queryable the way the live database is.
                for index in live.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
                    "AND sql IS NOT NULL",
                    (table,),
                ):
                    snapshot.execute(index["sql"])

            snapshot.execute(
                "CREATE TABLE snapshot_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            snapshot.executemany(
                "INSERT INTO snapshot_meta (key, value) VALUES (?, ?)",
                [
                    ("built_at", utcnow_naive().isoformat(timespec="seconds")),
                    ("built_by", "tools/build_snapshot.py"),
                    ("source", "the live oilwatch.sqlite, which is not committed"),
                    (
                        "withheld_tables",
                        "; ".join(f"{name} ({why})" for name, why in WITHHELD_TABLES.items()),
                    ),
                    (
                        "redacted",
                        "; ".join(
                            f"{table}.{column}: {why}" for (table, column), why in WITHHELD_VALUES.items()
                        ),
                    ),
                    (
                        "note",
                        "The owner's own details are replaced with <redacted-...> "
                        "wherever they survived a kept note.",
                    ),
                ],
            )
    finally:
        live.close()
        snapshot.close()

    return written


def leftovers(source: Path, target: Path, replacements: list[tuple[str, str]]) -> list[str]:
    """Anything that should not be in the snapshot and is.

    Checks the values that identify the owner, then samples from each dropped
    table - domains, message ids, an order reference. A sample is evidence: those
    values cannot be reconstructed from the kept tables, so finding one in the
    snapshot means a page or a column carried it across.
    """
    wanted = [needle for needle, _ in replacements]

    live = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    live.row_factory = sqlite3.Row
    for table, column in (
        ("sender_judgements", "domain"),
        ("processed_messages", "message_id"),
        ("orders", "reference"),
    ):
        try:
            rows = live.execute(f"SELECT {column} AS v FROM {table} LIMIT 5").fetchall()
        except sqlite3.Error:
            continue
        wanted.extend(str(row["v"]) for row in rows if row["v"])
    live.close()

    found: list[str] = []
    snapshot = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    snapshot.row_factory = sqlite3.Row
    tables = [
        row["name"]
        for row in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")
    ]
    for table in tables:
        for column in (row["name"] for row in snapshot.execute(f"PRAGMA table_info({table})")):
            try:
                values = snapshot.execute(
                    f"SELECT {column} AS v FROM {table} WHERE {column} LIKE '%'"
                ).fetchall()
            except sqlite3.Error:
                continue
            for row in values:
                text = row["v"]
                if not isinstance(text, str):
                    continue
                for needle in wanted:
                    if needle and needle in text:
                        found.append(f"{table}.{column} carries {needle!r}")
    snapshot.close()

    # The bytes as well as the columns: free pages and the WAL are not columns.
    blob = target.read_bytes()
    for needle in wanted:
        if needle and needle.encode("utf-8") in blob:
            found.append(f"the file's bytes carry {needle!r}")
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="print what would be written only")
    args = parser.parse_args()

    if args.check:
        live = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)
        for table, columns in KEPT_COLUMNS.items():
            count = live.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{table:<16} {count:>6} rows   {len(columns)} columns kept")
        print(f"withheld         {', '.join(WITHHELD_TABLES)}")
        live.close()
        return

    written = build(SOURCE, TARGET)
    print(f"wrote {TARGET} ({TARGET.stat().st_size / 1024:.0f} KiB)")
    for table, count in written.items():
        print(f"  {table:<16} {count:>6} rows")

    contact = load_contact()
    replacements = redactions(contact, load_settings().home.label)
    found = leftovers(SOURCE, TARGET, replacements)
    if found:
        print("\nthe snapshot still carries something it should not:")
        for line in dict.fromkeys(found):
            print(f"  {line}")
        TARGET.unlink()
        raise SystemExit("snapshot deleted; nothing was left behind to commit")

    checked = ", ".join(needle for needle, _ in replacements)
    print(f"\nverified absent from every column and from the file's bytes: {checked}")
    print("and sampled values from sender_judgements, processed_messages and orders")


if __name__ == "__main__":
    main()
