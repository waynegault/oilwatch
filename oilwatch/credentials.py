"""Credential manager for supplier account credentials."""

from __future__ import annotations

import base64
import contextlib
import json
import os
import secrets
import string
import tempfile
import threading
from pathlib import Path
from typing import Any

from oilwatch import secretstore
from oilwatch.config import CHECKOUT_ROOT
from oilwatch.identity import load_contact
from oilwatch.models import utcnow_naive

ENVELOPE_KEY = "format"
ENVELOPE_FORMAT = "dpapi"

#: Serialises the mutate-then-write pair below. Connectors quote several at
#: once on a ThreadPoolExecutor, and the store is one process-wide manager, so
#: two of them can reach `set_credentials` together: without this, one thread can
#: be encoding the dict in `_save` while another inserts into it, which is a
#: "dictionary changed size during iteration" rather than a tidy last-write-wins.
#: Reentrant, because the mutate and the save each take it — nesting is the point.
#: A lock per process cannot cover the detached `quote-all` worker, whose store is
#: its own; `_write_atomically` is what keeps a second writer from being read
#: mid-write.
_WRITE_LOCK = threading.RLock()


class CredentialStoreUnreadable(RuntimeError):
    """The credentials file exists but could not be read.

    Raised rather than returning an empty store, because the two are not the
    same thing: a store that reads as empty leads the quote path to *generate a
    fresh password and write it over the file*, which desynchronises a real
    supplier account whose password was in there.
    """


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
            # The checkout's store, not the process working directory: the MCP
            # server is spawned from elsewhere, and a cwd-relative store meant a
            # spawned run generated a fresh password into the wrong directory
            # instead of reading the account it was supposed to sign in with.
            config_path = CHECKOUT_ROOT / "config" / "supplier_credentials.json"
        self.config_path = config_path
        self._credentials: dict[str, dict[str, Any]] = {}
        self._encrypted = False
        self.load_error: str | None = None
        self._encrypt_on_save = secretstore.available() if encrypt is None else encrypt
        self._load()

    def _load(self) -> None:
        """Load credentials, accepting either the DPAPI envelope or plain JSON.

        Every way of failing lands in ``load_error`` rather than reading as an
        empty store. The failure that matters is a *truncated* file — the shape a
        write being read mid-flight leaves behind — because "no credentials" is
        indistinguishable from "nothing stored yet" to everything downstream, and
        the quote path responds to it by generating a new password.
        """
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
        except json.JSONDecodeError as exc:
            self.load_error = f"could not parse {self.config_path}: {exc}"
            return

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

        if not isinstance(payload, dict):
            # Valid JSON of the wrong shape, e.g. a list: still not a store we
            # can read, and still not a reason to overwrite it.
            self.load_error = f"{self.config_path} is not a credentials store"
            return

        self._credentials = payload.get("credentials", {})

    def _write_atomically(self, text: str) -> None:
        """Replace the store in one step, so no reader sees a half-written file.

        ``write_text`` truncates the target first: a second writer — the quote
        sweeps run several connectors at once, and a background sweep is a
        separate process with its own manager — can be read between the truncate
        and the write, and a truncated store reads as no store at all. A temp
        file in the same directory plus ``os.replace`` is atomic on Windows and
        POSIX, so the file is either the old contents or the new ones.
        """
        directory = self.config_path.parent
        directory.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(text)
            os.replace(temp_name, self.config_path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp_name)
            raise

    def _save(self) -> None:
        """Persist credentials, encrypting when the platform supports it.

        Refuses when the store could not be read: the file may hold other
        suppliers' accounts, so writing would replace them with the one entry
        this process happens to know about — and for the supplier whose password
        was in the unreadable file, replace a working credential with a fresh
        one. Repairing or deleting the file is a person's decision, so the error
        names it and stops.
        """
        if self.load_error is not None:
            raise CredentialStoreUnreadable(
                f"refusing to write {self.config_path}: {self.load_error}. Move or "
                "delete that file to start over, or repair it first."
            )
        payload = {"email": load_contact().email, "credentials": self._credentials}

        with _WRITE_LOCK:
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
                self._write_atomically(json.dumps(envelope, indent=2))
                self._encrypted = True
                return

            self._write_atomically(json.dumps(payload, indent=2))
            self._encrypted = False

    @property
    def is_encrypted(self) -> bool:
        """True when the file on disk is the DPAPI envelope."""
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
        entry = {
            "email": email or load_contact().email,
            "password": password,
            "notes": notes,
            # A real timestamp: this used to hold a random hex token, so nothing
            # could read a creation time back out of a field named created_at.
            "created_at": utcnow_naive().isoformat(),
        }
        with _WRITE_LOCK:
            # The assignment is inside the lock with the save, not just the save:
            # `_save` encodes the whole dict, and an insert from another thread
            # part-way through that encode raises "dictionary changed size during
            # iteration" — instead of writing what both threads stored.
            self._credentials[supplier_key] = entry
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
