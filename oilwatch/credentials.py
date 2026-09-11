"""Credential manager for supplier account credentials."""

from __future__ import annotations

import base64
import json
import secrets
import string
from pathlib import Path
from typing import Any

from oilwatch import secretstore
from oilwatch.identity import load_contact

ENVELOPE_KEY = "format"
ENVELOPE_FORMAT = "dpapi"


def generate_password(length: int = 8) -> str:
    """Generate a random password with letters and digits."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_supplier_password(supplier_name: str) -> str:
    """
    Generate a password in the format: [suppliername]!8character_random

    Example: ScottishFuels!aB3xK9mQ
    """
    # Keep letters and digits only, then title-case. The join already dropped
    # any spaces, so there is nothing left to strip.
    clean_name = "".join(c for c in supplier_name if c.isalnum()).title()
    # Generate 8 random characters
    random_part = generate_password(8)
    return f"{clean_name}!{random_part}"


class CredentialManager:
    """
    Manages supplier account credentials.

    Credentials are written to ``config/supplier_credentials.json``, encrypted at
    rest with Windows DPAPI (see ``oilwatch.secretstore``) so the file is readable
    only by this Windows account on this machine. Legacy plain-JSON files are
    still read, and are rewritten encrypted the next time anything is saved.

    If DPAPI is unavailable — a non-Windows platform — the file is written as
    plain text instead, because refusing to run would lock the owner out of their
    own credentials. The file is gitignored either way.
    """

    def __init__(self, config_path: Path | None = None, *, encrypt: bool | None = None) -> None:
        if config_path is None:
            config_path = Path.cwd() / "config" / "supplier_credentials.json"
        self.config_path = config_path
        self._credentials: dict[str, dict[str, Any]] = {}
        self._encrypted = False
        self.load_error: str | None = None
        self._encrypt_on_save = secretstore.available() if encrypt is None else encrypt
        self._load()

    def _load(self) -> None:
        """Load credentials, accepting either the DPAPI envelope or plain JSON."""
        self._credentials = {}
        self._encrypted = False
        if not self.config_path.exists():
            return
        try:
            raw = self.config_path.read_text(encoding="utf-8")
        except OSError as exc:
            self.load_error = f"could not read {self.config_path}: {exc}"
            return

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None

        if isinstance(payload, dict) and payload.get(ENVELOPE_KEY) == ENVELOPE_FORMAT:
            self._encrypted = True
            try:
                plain = secretstore.unprotect(base64.b64decode(payload["blob"]))
                payload = json.loads(plain.decode("utf-8"))
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                # Nearly always: written by a different Windows account or on
                # another machine, where DPAPI cannot derive the same key.
                self.load_error = f"could not decrypt {self.config_path}: {exc}"
                return

        if isinstance(payload, dict):
            self._credentials = payload.get("credentials", {})

    def _save(self) -> None:
        """Persist credentials, encrypting when the platform supports it."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"email": load_contact().email, "credentials": self._credentials}

        if self._encrypt_on_save and secretstore.available():
            plain = json.dumps(payload, indent=2).encode("utf-8")
            envelope = {
                ENVELOPE_KEY: ENVELOPE_FORMAT,
                "hint": (
                    "Encrypted with Windows DPAPI: readable only by this Windows "
                    "account on this machine. Delete the file to start over."
                ),
                "blob": base64.b64encode(secretstore.protect(plain)).decode("ascii"),
            }
            self.config_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
            self._encrypted = True
            return

        self.config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._encrypted = False

    @property
    def is_encrypted(self) -> bool:
        """True when the file on disk is the DPAPI envelope."""
        return self._encrypted

    def resave(self) -> bool:
        """Rewrite the file in the preferred format. Returns True if encrypted.

        Converting an existing plain-text file is just load-then-save.
        """
        self._save()
        return self._encrypted

    def get_credentials(self, supplier_key: str) -> dict[str, str] | None:
        """
        Get credentials for a supplier.

        Args:
            supplier_key: Supplier identifier (e.g., 'scottish_fuels', 'valueoils')

        Returns:
            Dict with 'email' and 'password' keys, or None if not found
        """
        creds = self._credentials.get(supplier_key)
        if creds:
            return {
                "email": creds.get("email") or load_contact().email,
                "password": creds.get("password", ""),
            }
        return None

    def set_credentials(
        self,
        supplier_key: str,
        password: str,
        email: str | None = None,
        notes: str = "",
    ) -> None:
        """
        Store credentials for a supplier.

        Args:
            supplier_key: Supplier identifier
            password: Account password
            email: Account email (defaults to the configured contact email)
            notes: Optional notes about the account
        """
        self._credentials[supplier_key] = {
            "email": email or load_contact().email,
            "password": password,
            "notes": notes,
            "created_at": secrets.token_hex(8),
        }
        self._save()

    def generate_and_store_password(
        self,
        supplier_key: str,
        supplier_name: str,
        email: str | None = None,
    ) -> str:
        """
        Generate a password and store credentials.

        Args:
            supplier_key: Supplier identifier
            supplier_name: Human-readable supplier name
            email: Account email (defaults to the configured contact email)

        Returns:
            Generated password
        """
        password = generate_supplier_password(supplier_name)
        self.set_credentials(
            supplier_key=supplier_key,
            password=password,
            email=email,
            notes=f"Auto-generated for {supplier_name}",
        )
        return password

    def list_suppliers(self) -> list[str]:
        """List all supplier keys with stored credentials."""
        return list(self._credentials.keys())

    def has_credentials(self, supplier_key: str) -> bool:
        """Check if credentials exist for a supplier."""
        return supplier_key in self._credentials


# Default credential manager instance
_default_manager: CredentialManager | None = None


def get_credential_manager() -> CredentialManager:
    """Get the default credential manager instance."""
    global _default_manager
    if _default_manager is None:
        _default_manager = CredentialManager()
    return _default_manager


def get_supplier_credentials(supplier_key: str) -> dict[str, str] | None:
    """Get credentials for a supplier."""
    return get_credential_manager().get_credentials(supplier_key)


def store_supplier_credentials(
    supplier_key: str,
    password: str,
    supplier_name: str = "",
    email: str | None = None,
) -> None:
    """Store credentials for a supplier."""
    manager = get_credential_manager()
    if supplier_name:
        manager.set_credentials(supplier_key, password, email, f"Auto-generated for {supplier_name}")
    else:
        manager.set_credentials(supplier_key, password, email)
