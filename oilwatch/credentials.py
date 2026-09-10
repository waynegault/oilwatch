"""Credential manager for supplier account credentials."""

from __future__ import annotations

import json
import secrets
import string
from pathlib import Path
from typing import Any

from oilwatch.identity import load_contact


def generate_password(length: int = 8) -> str:
    """Generate a random password with letters and digits."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_supplier_password(supplier_name: str) -> str:
    """
    Generate a password in the format: [suppliername]!8character_random

    Example: ScottishFuels!aB3xK9mQ
    """
    # Clean supplier name - remove spaces and special chars
    clean_name = "".join(c for c in supplier_name if c.isalnum())
    # Capitalize first letter of each word
    clean_name = clean_name.title().replace(" ", "")
    # Generate 8 random characters
    random_part = generate_password(8)
    return f"{clean_name}!{random_part}"


class CredentialManager:
    """
    Manages supplier account credentials.

    Passwords are stored as **plain text** in ``config/supplier_credentials.json``
    because the browser connectors have to supply the real password to sign in.
    That file is gitignored, but it is not encrypted — encrypting it at rest is
    an open item, so treat it like any other password file.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        if config_path is None:
            config_path = Path.cwd() / "config" / "supplier_credentials.json"
        self.config_path = config_path
        self._credentials: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        """Load credentials from file."""
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                self._credentials = data.get("credentials", {})
            except (json.JSONDecodeError, IOError):
                self._credentials = {}

    def _save(self) -> None:
        """Save credentials to file."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "email": load_contact().email,
            "credentials": self._credentials,
        }
        self.config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

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
