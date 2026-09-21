"""Microsoft Graph email monitor (OAuth2) for supplier quote replies.

Outlook.com now requires OAuth2 (Microsoft has disabled basic/IMAP password
auth). This module uses the Microsoft Graph API instead: it authenticates via
the OAuth2 device-code flow (``msal``), polls the inbox for unread supplier
replies, extracts the price, records it, and deletes the message.

Requires a Microsoft Entra app registration:

* Client ID is read from the ``MICROSOFT_CLIENT_ID`` environment variable.
* The app must be a public client (allow public client flows) with the
  delegated ``Mail.ReadWrite`` permission.
* One-time authentication: ``oilwatch login-email`` (device-code flow).
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import msal

from oilwatch import secretstore
from oilwatch.credentials import ENVELOPE_FORMAT, ENVELOPE_KEY
from oilwatch.email_parsing import extract_ppl, supplier_fragment_for
from oilwatch.logging_setup import get_logger
from oilwatch.pricing import DOMESTIC_VAT_RATE, apply_vat, inclusive_total

log = get_logger("graph_email")

SCOPES = ["Mail.ReadWrite"]
AUTHORITY = "https://login.microsoftonline.com/consumers"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"


def load_client_id() -> str:
    """The Entra app (client) id for the mailbox grant.

    Not a secret — it is a public OAuth client id, already written out in
    monitor_email.bat — so it is allowed to live in settings.json. That means
    `oilwatch monitor-email` works on its own, instead of failing unless it is
    launched through that batch file.
    """
    client_id = os.environ.get("MICROSOFT_CLIENT_ID")
    if not client_id:
        try:
            from oilwatch.config import load_settings

            client_id = load_settings().microsoft_client_id
        except Exception:  # noqa: BLE001 - a missing settings file is not the problem here
            client_id = ""
    if not client_id:
        raise RuntimeError(
            "Set the MICROSOFT_CLIENT_ID environment variable (the Entra app "
            "registration Application (client) ID), or add microsoft_client_id "
            "to config/settings.json."
        )
    return client_id


def cache_path() -> Path:
    """Where the mailbox refresh token is cached; encrypted at rest when DPAPI runs."""
    return Path.home() / ".oilwatch" / "graph_token_cache.json"


def sender_domain_from_email(email_addr: str) -> str:
    match = re.search(r"@([\w.\-]+)", email_addr or "")
    return match.group(1).lower() if match else ""


class GraphEmailMonitor:
    def __init__(self, client_id: str | None = None) -> None:
        self.client_id = client_id or load_client_id()
        self.app = msal.PublicClientApplication(self.client_id, authority=AUTHORITY)

    def _save_refresh_token(self, result: dict[str, Any]) -> None:
        refresh_token = result.get("refresh_token") if result else None
        if not refresh_token:
            return
        path = cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"refresh_token": refresh_token}).encode("utf-8")
        if secretstore.available():
            # Same DPAPI envelope as the supplier credentials: a refresh token
            # grants Mail.ReadWrite, so it must not sit in the file in the
            # clear. Fall back to plain text off Windows rather than refusing to
            # cache the token at all (see oilwatch.secretstore).
            envelope = {
                ENVELOPE_KEY: ENVELOPE_FORMAT,
                "hint": (
                    "Encrypted with Windows DPAPI: readable only by this Windows "
                    "account on this machine. Delete the file to sign in again."
                ),
                "blob": base64.b64encode(secretstore.protect(payload)).decode("ascii"),
            }
            path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
            return
        path.write_text(payload.decode("utf-8"), encoding="utf-8")

    def _load_refresh_token(self) -> str | None:
        path = cache_path()
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get(ENVELOPE_KEY) == ENVELOPE_FORMAT:
                # An envelope from another Windows account or machine cannot be
                # decrypted here; that reads as "no token, sign in again".
                plain = secretstore.unprotect(base64.b64decode(payload["blob"]))
                payload = json.loads(plain.decode("utf-8"))
            if isinstance(payload, dict):
                return payload.get("refresh_token")
        except Exception:  # noqa: BLE001 - a corrupt or foreign cache is a sign-in prompt
            return None
        return None

    def interactive_login(self) -> dict[str, Any]:
        """Run the OAuth2 device-code flow and cache the refresh token."""
        flow = self.app.initiate_device_flow(scopes=SCOPES)
        print(flow["message"], flush=True)  # prints the URL + user code
        result = self.app.acquire_token_by_device_flow(flow)
        self._save_refresh_token(result)
        return result

    def get_token(self) -> dict[str, Any] | None:
        # 1. silent refresh from the in-memory cache (same process)
        accounts = self.app.get_accounts()
        if accounts:
            result = self.app.acquire_token_silent(SCOPES, account=accounts[0])
            if result:
                self._save_refresh_token(result)
                return result
        # 2. refresh token persisted on disk
        refresh_token = self._load_refresh_token()
        if refresh_token:
            result = self.app.acquire_token_by_refresh_token(refresh_token, SCOPES)
            if result and "access_token" in result:
                self._save_refresh_token(result)
                return result
        return None

    def _headers(self, token: dict[str, Any]) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token['access_token']}",
            # Immutable ids do not change when a message moves between folders.
            # Without this, deleting a processed reply hands it a new id in
            # Deleted Items, the next sweep reads that as unseen mail, and the
            # same quote is recorded a second time.
            "Prefer": 'IdType="ImmutableId"',
        }

    def fetch_unseen(self, token: dict[str, Any]) -> list[dict[str, Any]]:
        response = httpx.get(
            f"{GRAPH_ENDPOINT}/me/mailFolders/inbox/messages",
            headers=self._headers(token),
            params={
                "$filter": "isRead eq false",
                "$top": "50",
                "$select": "id,from,subject,body,bodyPreview,receivedDateTime",
            },
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json().get("value", [])

    def fetch_candidates(self, token: dict[str, Any]) -> list[dict[str, Any]]:
        """Messages worth scanning: read or unread, live or already deleted.

        Nothing is filtered by read state — a price or a code is just as useful
        in mail that has already been opened — and Deleted Items is swept as
        well, because a supplier reply is easy to delete by accident. Mail that
        has been purged from Deleted Items is attempted too and skipped quietly
        where the mailbox will not expose it.

        Re-reading old mail is only safe because the caller keeps a ledger of
        processed message ids; otherwise every run would re-record old quotes.
        """
        collected: dict[str, dict[str, Any]] = {}
        for path in ("/me/messages", "/me/mailFolders/recoverableitemsdeletions/messages"):
            try:
                response = httpx.get(
                    f"{GRAPH_ENDPOINT}{path}",
                    headers=self._headers(token),
                    params={
                        "$top": "100",
                        "$select": "id,from,subject,body,bodyPreview,receivedDateTime,parentFolderId",
                    },
                    timeout=30.0,
                )
                response.raise_for_status()
            except Exception as exc:  # noqa: BLE001 - one unavailable folder must not stop the sweep
                log.debug("skipping %s: %s", path, exc)
                continue
            for message in response.json().get("value", []):
                if message.get("id"):
                    collected.setdefault(message["id"], message)
        return list(collected.values())

    def inbox_id(self, token: dict[str, Any]) -> str | None:
        """The inbox folder id, so we only ever delete from the inbox itself."""
        try:
            response = httpx.get(
                f"{GRAPH_ENDPOINT}/me/mailFolders/inbox",
                headers=self._headers(token),
                params={"$select": "id"},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json().get("id")
        except Exception as exc:  # noqa: BLE001 - without it we simply delete nothing
            log.debug("could not resolve the inbox folder: %s", exc)
            return None

    def delete(self, token: dict[str, Any], message_id: str) -> None:
        httpx.delete(
            f"{GRAPH_ENDPOINT}/me/messages/{message_id}",
            headers=self._headers(token),
            timeout=30.0,
        ).raise_for_status()

    def run(self, app: Any) -> list[dict[str, Any]]:
        # Make sure the schema exists for direct callers too. The service path
        # does this, but anything calling run() directly used to fail with
        # "no such table: processed_messages" on an older database.
        app.db.init_schema()

        token = self.get_token()
        if token is None:
            raise RuntimeError("Not authenticated. Run `oilwatch login-email` first.")

        recorded: list[dict[str, Any]] = []
        inbox = self.inbox_id(token)
        scanned = 0
        unrecognised = 0
        candidates: set[str] = set()
        for message in self.fetch_candidates(token):
            message_id = message.get("id", "")
            if app.db.message_processed(message_id):
                continue  # already mined; re-reading old mail must not duplicate
            scanned += 1
            sender = message.get("from", {}).get("emailAddress", {}).get("address", "")
            domain = sender_domain_from_email(sender)
            supplier_fragment = supplier_fragment_for(domain)
            if supplier_fragment is None:
                # A sender the map does not know. It is counted but not named:
                # most of this is personal correspondence, and the log is
                # written to disk - a first attempt at naming them put eighty
                # of the owner's correspondents, including a financial
                # ombudsman case and his bank, into one line. The one case
                # worth naming is a message that reads like a fuel price,
                # because that is an oil company being missed rather than a
                # stranger.
                unrecognised += 1
                if extract_ppl(self._body_text(message)) is not None:
                    candidates.add(domain)
                continue
            supplier = self._find_supplier(app, supplier_fragment)
            if supplier is None:
                # The domain is one we know, but no supplier row carries it, so
                # there is nowhere to file a price. Silent until 2026-09-21, and
                # that silence was visible: a sweep's own arithmetic did not add
                # up, because scanned minus unrecognised counts the messages that
                # should each leave a line and one of them left none. Which of
                # this branch and the already-recorded one had taken it was
                # unknowable from the log - the same defect as the unrecognised
                # senders above, one layer in.
                log.warning(
                    "mail from %s maps to supplier fragment %r, which no supplier row "
                    "matches; skipped",
                    domain,
                    supplier_fragment,
                )
                continue

            text = self._body_text(message)

            # Capture discount codes before anything is deleted: the message is
            # removed once processed, so an uncaptured code is gone for good.
            # A discount-only email (no price in it) still earns its keep here.
            from oilwatch.discounts import looks_like_an_offer, parse_discounts
            from oilwatch.models import utcnow_naive

            stamp = utcnow_naive()
            raw_stamp = message.get("receivedDateTime") or ""
            if raw_stamp:
                with contextlib.suppress(ValueError):
                    # Graph sends "...Z", which fromisoformat accepts on 3.11+; the
                    # value is kept naive like the rest of the app so expiry
                    # comparisons never mix aware and naive datetimes.
                    stamp = datetime.fromisoformat(raw_stamp).replace(tzinfo=None)

            offers = parse_discounts(text, received_at=stamp)
            for offer in offers:
                app.db.record_discount(
                    {
                        "supplier_id": supplier["id"],
                        "code": offer.code,
                        "amount_gbp": offer.amount_gbp,
                        "min_litres": offer.min_litres,
                        "max_litres": offer.max_litres,
                        "expires_at": offer.expires_at.isoformat() if offer.expires_at else None,
                        "terms": offer.terms,
                        "source": "email",
                        "observed_at": stamp.isoformat(),
                    }
                )

            ex_vat = extract_ppl(text)
            if ex_vat is None and not offers:
                # Nothing to record. Clear it out of the inbox so supplier mail
                # does not pile up, but deliberately do NOT mark it processed:
                # it stays in Deleted Items, still visible to a later sweep and
                # to any future parsing improvement.
                #
                # Said whether or not the message is in the inbox. These lines
                # used to sit inside the branch that deletes, so supplier mail
                # that was not in the inbox - a newsletter already in Deleted
                # Items - was re-scanned every sweep without a word, and that is
                # what made a sweep's own arithmetic fail to add up on
                # 2026-09-21: scanned minus unrecognised counts the messages that
                # should each leave a line, and one of them left none, every hour.
                if inbox and message.get("parentFolderId") == inbox:
                    self.delete(token, message_id)
                    outcome = "cleared from the inbox"
                else:
                    outcome = "left where it is"
                if looks_like_an_offer(text):
                    # It reads like an offer but no rule could read it, so it may
                    # be a discount in a format we cannot parse yet. Say so rather
                    # than lose it in silence; it stays re-scannable until a rule
                    # for that format exists.
                    log.warning(
                        "possible offer no rule could read in %r from %s; %s",
                        message.get("subject", ""),
                        domain,
                        outcome,
                    )
                else:
                    log.info(
                        "nothing to record in %r from %s; %s",
                        message.get("subject", ""),
                        domain,
                        outcome,
                    )
                continue

            if ex_vat is not None:
                price_per_liter = apply_vat(ex_vat, DOMESTIC_VAT_RATE)
                quantity = app.settings.quote_quantity_liters
                record = {
                    "supplier_id": supplier["id"],
                    # The email's own date, not now: now that the sweep reaches
                    # back through old mail, dating an old reply today would let a
                    # stale price pass the recency window as if it were current.
                    "observed_at": stamp.isoformat(),
                    "quantity_liters": quantity,
                    "status": "ok",
                    "price_per_liter": price_per_liter,
                    "total_price": inclusive_total(price_per_liter, quantity),
                    "currency": app.settings.currency,
                    "source": "email",
                    "notes": f"From email reply ({domain})",
                    "raw_payload": {"from": sender, "subject": message.get("subject", "")},
                }
                # A reply that was already mined can arrive here again when its
                # id changed on a folder move; the same observation is not a
                # second quote, and must not be reported as one either.
                if not app.db.quote_already_recorded(record):
                    app.db.record_quote(record)
                    recorded.append(record)
                    log.info(
                        "recorded %.4f/L from %s (%s)",
                        price_per_liter,
                        supplier["name"],
                        domain,
                    )
                else:
                    # Expected when a reply's id changes on a folder move, so
                    # this is not a warning - but it is the other branch that
                    # used to swallow a message without a word.
                    log.info(
                        "the same observation from %s (%s) is already recorded; cleared",
                        supplier["name"],
                        domain,
                    )

            app.db.mark_message_processed(message_id)
            # Delete only from the inbox. Mail already sitting in Deleted Items is
            # left there, so sweeping the bin can never become a permanent purge.
            if inbox and message.get("parentFolderId") == inbox:
                self.delete(token, message_id)

        # Always leave a record of the sweep. Recording a quote used to log
        # nothing at all, so an unattended run that captured a reply and one
        # that saw no mail were equally silent - and this log is the only thing
        # a scheduled run leaves behind. On 2026-09-18 it was empty back to the
        # 13th while sweeps were running hourly and recording quotes.
        log.info(
            "sweep: scanned %d, recorded %d, %d from unrecognised sender(s)",
            scanned,
            len(recorded),
            unrecognised,
        )
        if candidates:
            # Fuel-shaped mail from a sender the map does not know: this is the
            # one case where an oil company is being missed rather than a
            # stranger, so it is named and the fix is spelled out.
            log.warning(
                "mail that reads like a fuel quote from unrecognised sender(s): %s "
                "- add the domain to SUPPLIER_DOMAINS to record them",
                ", ".join(sorted(candidates)),
            )
        return recorded

    @staticmethod
    def _body_text(message: dict[str, Any]) -> str:
        body = message.get("body") or {}
        content = body.get("content")
        if not content:
            return message.get("bodyPreview", "")
        if body.get("contentType") == "text":
            return content
        # HTML -> plain text (a crude strip is fine for price extraction).
        text = re.sub(r"<style[\s\S]*?</style>", " ", content, flags=re.IGNORECASE)
        text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&pound;", "£")
        return re.sub(r"\s+", " ", text)

    @staticmethod
    def _find_supplier(app: Any, fragment: str) -> dict[str, Any] | None:
        for supplier in app.db.list_suppliers(include_inactive=True):
            if fragment in (supplier.get("website") or "").lower():
                return supplier
        return None
