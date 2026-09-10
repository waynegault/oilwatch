"""Email monitoring: read supplier quote replies, record them, then delete.

Suppliers that only quote by form/email (Gleaner Oils, Oilfast, Highland Fuels,
Regency Oils) reply by email with their price.

**The live path is Microsoft Graph, in ``graph_email.py``** — Outlook no longer
accepts basic/IMAP password authentication, so ``EmailMonitor`` below cannot
connect to a real mailbox and is reachable from no entry point; ``oilwatch
monitor-email`` runs the Graph monitor instead. What is still used from this
module is the supplier domain map and the price parser (``SUPPLIER_DOMAINS``,
``extract_ppl``), which Graph reuses.

Only ``extract_ppl`` and ``SUPPLIER_DOMAINS`` should be imported from here. The
IMAP class and its configuration still describe this interface::

    MICROSOFT_PASSWORD    (legacy, no longer authenticates anything)
    MICROSOFT_EMAIL       - the mailbox to poll (else the configured contact email)
    MICROSOFT_IMAP_SERVER (default outlook.office365.com)
    MICROSOFT_IMAP_PORT   (default 993)
"""

from __future__ import annotations

import email
import imaplib
import json
import os
import re
from datetime import datetime
from email.header import decode_header
from pathlib import Path
from typing import Any

from oilwatch.identity import load_contact
from oilwatch.pricing import DOMESTIC_VAT_RATE, apply_vat, inclusive_total, pence_to_pounds

# Map an email sender domain to the supplier website fragment used by the DB.
SUPPLIER_DOMAINS = {
    "gleaner.co.uk": "gleaner.co.uk",
    "oilfast.co.uk": "oilfast.co.uk",
    "highlandfuels.co.uk": "highlandfuels.co.uk",
    "regencyoils.com": "regencyoils.com",
    "regencyoils.co.uk": "regencyoils.com",  # replies arrive from the .co.uk domain
    "scottishfuels.co.uk": "scottishfuels.co.uk",
    "certasenergy.co.uk": "scottishfuels.co.uk",  # Scottish Fuels = Certas Energy
    "rix.co.uk": "rix.co.uk",
    "brogans.co.uk": "brogans.co.uk",
    "connon-oils.co.uk": "connon",  # Connon Bros/Oils (Fuelsoft, website connon.fuelsoft.co.uk)
    "connon.fuelsoft.co.uk": "connon",
    # Senders whose mail carries prices or discount codes. ValueOils was missing,
    # which meant its emails were skipped before anything was parsed — including
    # the discount offers it sends.
    "valueoils.com": "valueoils.com",
    "homefuelsdirect.co.uk": "homefuelsdirect.co.uk",
    "crownoil.co.uk": "crownoil.co.uk",
    "nationwidefuels.co.uk": "nationwidefuels.co.uk",
    "compassfuels.co.uk": "compassfuels.co.uk",
}

# Price patterns found in supplier reply emails.
PPL_PATTERNS = [
    r"(\d+(?:\.\d{1,2})?)\s*p(?:ence)?\s*(?:per\s*/?\s*litre|/l)\b",
    r"(\d+(?:\.\d{1,2})?)\s*p\s*\(?excl?\.?\s*vat\)?",
    r"price\s*per\s*litre[^£\n]*[£]?\s*(\d+(?:\.\d{1,2})?)\s*p",
    r"£\s?(\d+(?:\.\d{1,2})?)\s*(?:per\s*/?\s*litre|/l)\b",
    # "107.50PPL" — Highland Fuels states the unit price as pence per litre with
    # a PPL suffix and no "per litre" wording.
    r"(\d+(?:\.\d{1,2})?)\s*ppl\b",
]


def load_email_config(root: Path | None = None) -> dict[str, Any]:
    password = os.environ.get("MICROSOFT_PASSWORD")
    if password:
        return {
            "email": os.environ.get("MICROSOFT_EMAIL") or load_contact().email,
            "password": password,
            "imap_server": os.environ.get("MICROSOFT_IMAP_SERVER", "outlook.office365.com"),
            "imap_port": int(os.environ.get("MICROSOFT_IMAP_PORT", "993")),
        }

    # Fallback: legacy JSON config file (gitignored).
    base = root or Path.cwd()
    path = base / "config" / "email_credentials.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    raise RuntimeError(
        "Set the MICROSOFT_PASSWORD environment variable "
        "(or create config/email_credentials.json)."
    )


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for text, charset in parts:
        if isinstance(text, bytes):
            out.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def _body_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return ""


def sender_domain(msg: email.message.Message) -> str:
    from_ = _decode(msg.get("From", ""))
    match = re.search(r"@([\w.\-]+)", from_)
    return match.group(1).lower() if match else ""


def extract_ppl(text: str) -> float | None:
    """Extract the price per litre (GBP, ex-VAT) from an email body.

    Handles the three formats actually seen in supplier replies:

    * Fuelsoft emails list the unit price as ``£1.0481`` (a £ value with four
      decimals; totals use only two).
    * Rix writes ``Price per litre 110.35`` (pence, no unit suffix).
    * Scottish Fuels writes ``Price Per Litre (Excl. VAT): 101.03p`` (pence).

    Returns ``None`` if no price is found.
    """
    # Fuelsoft-style £ per litre with exactly 4 decimals.
    pounds4 = re.findall(r"£\s?(\d+\.\d{4})\b", text)
    if pounds4:
        return round(min(float(v) for v in pounds4), 4)

    # "Price per litre ..." — the number is pence in the replies seen so far.
    m = re.search(r"price\s*per\s*litre[^\d£]{0,60}?(\d+(?:\.\d{1,2})?)", text, re.IGNORECASE)
    if m:
        return pence_to_pounds(m.group(1))

    for pattern in PPL_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            raw = m.group(1)
            value = float(raw)
            # pence vs pounds heuristic: >100 => pence
            if value > 100:
                return pence_to_pounds(value)
            if value < 10:
                return round(value, 4)
            # 10..100 is ambiguous; treat as pence (oil ~90-160p)
            return pence_to_pounds(value)
    return None


class EmailMonitor:
    def __init__(self, root: Path | None = None) -> None:
        self.config = load_email_config(root)
        self.root = root or Path.cwd()
        self.connection: imaplib.IMAP4_SSL | None = None

    def connect(self) -> imaplib.IMAP4_SSL:
        server = self.config.get("imap_server", "outlook.office365.com")
        port = int(self.config.get("imap_port", 993))
        self.connection = imaplib.IMAP4_SSL(server, port)
        self.connection.login(self.config["email"], self.config["password"])
        self.connection.select("INBOX")
        return self.connection

    def disconnect(self) -> None:
        if self.connection is not None:
            try:
                self.connection.logout()
            except Exception:  # noqa: BLE001
                pass
            self.connection = None

    def fetch_unseen(self) -> list[tuple[str, email.message.Message]]:
        """Return ``(uid, Message)`` for each unseen email in the inbox."""
        assert self.connection is not None
        _, data = self.connection.uid("search", None, "UNSEEN")
        uids = data[0].split()
        results: list[tuple[str, email.message.Message]] = []
        for uid in uids:
            _, msg_data = self.connection.uid("fetch", uid, "(RFC822)")
            if not msg_data or msg_data[0] is None:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            results.append((uid.decode(), msg))
        return results

    def delete(self, uid: str) -> None:
        assert self.connection is not None
        self.connection.uid("store", uid, "+FLAGS", "\\Deleted")

    def expunge(self) -> None:
        assert self.connection is not None
        self.connection.expunge()

    def run(self, app: Any) -> list[dict[str, Any]]:
        """Poll the inbox, record matching supplier quotes, and delete them.

        Returns a list of recorded quote records.
        """
        self.connect()
        recorded: list[dict[str, Any]] = []
        try:
            for uid, msg in self.fetch_unseen():
                domain = sender_domain(msg)
                supplier_fragment = SUPPLIER_DOMAINS.get(domain)
                if supplier_fragment is None:
                    continue

                supplier = self._find_supplier(app, supplier_fragment)
                if supplier is None:
                    continue

                text = _body_text(msg)
                ex_vat = extract_ppl(text)
                if ex_vat is None:
                    continue

                price_per_liter = apply_vat(ex_vat, DOMESTIC_VAT_RATE)
                quantity = app.settings.quote_quantity_liters
                record = {
                    "supplier_id": supplier["id"],
                    "observed_at": datetime.now().isoformat(),
                    "quantity_liters": quantity,
                    "status": "ok",
                    "price_per_liter": price_per_liter,
                    "total_price": inclusive_total(price_per_liter, quantity),
                    "currency": app.settings.currency,
                    "source": "email",
                    "notes": f"From email reply ({domain})",
                    "raw_payload": {"from": sender_domain(msg), "subject": _decode(msg.get('Subject', ''))},
                }
                app.db.record_quote(record)
                recorded.append(record)
                self.delete(uid)

            self.expunge()
        finally:
            self.disconnect()
        return recorded

    @staticmethod
    def _find_supplier(app: Any, fragment: str) -> dict[str, Any] | None:
        for supplier in app.db.list_suppliers(include_inactive=True):
            if fragment in (supplier.get("website") or "").lower():
                return supplier
        return None
