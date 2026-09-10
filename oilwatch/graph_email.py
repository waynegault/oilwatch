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

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import msal

from oilwatch.email_monitor import SUPPLIER_DOMAINS, extract_ppl
from oilwatch.pricing import DOMESTIC_VAT_RATE, apply_vat, inclusive_total

SCOPES = ["Mail.ReadWrite"]
AUTHORITY = "https://login.microsoftonline.com/consumers"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"


def load_client_id() -> str:
    client_id = os.environ.get("MICROSOFT_CLIENT_ID")
    if not client_id:
        raise RuntimeError(
            "Set the MICROSOFT_CLIENT_ID environment variable "
            "(the Entra app registration Application (client) ID)."
        )
    return client_id


def cache_path() -> Path:
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
        path.write_text(json.dumps({"refresh_token": refresh_token}), encoding="utf-8")

    def _load_refresh_token(self) -> str | None:
        path = cache_path()
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8")).get("refresh_token")
            except Exception:  # noqa: BLE001
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
        return {"Authorization": f"Bearer {token['access_token']}"}

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

    def delete(self, token: dict[str, Any], message_id: str) -> None:
        httpx.delete(
            f"{GRAPH_ENDPOINT}/me/messages/{message_id}",
            headers=self._headers(token),
            timeout=30.0,
        ).raise_for_status()

    def run(self, app: Any) -> list[dict[str, Any]]:
        token = self.get_token()
        if token is None:
            raise RuntimeError("Not authenticated. Run `oilwatch login-email` first.")

        recorded: list[dict[str, Any]] = []
        for message in self.fetch_unseen(token):
            sender = message.get("from", {}).get("emailAddress", {}).get("address", "")
            domain = sender_domain_from_email(sender)
            supplier_fragment = SUPPLIER_DOMAINS.get(domain)
            if supplier_fragment is None:
                continue
            supplier = self._find_supplier(app, supplier_fragment)
            if supplier is None:
                continue

            text = self._body_text(message)
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
                "raw_payload": {"from": sender, "subject": message.get("subject", "")},
            }
            app.db.record_quote(record)
            recorded.append(record)
            self.delete(token, message["id"])

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
