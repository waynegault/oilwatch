"""Who OilWatch is buying oil for.

Supplier enquiry forms and account registrations need a real name, email,
phone and delivery postcode. Those are personal details, so they are never
defaulted in source: they are resolved from the environment first, then from
``config/contact.json`` (gitignored). ``config/contact.example.json`` shows the
shape to copy.

Environment overrides (useful for CI or a second address):

    OILWATCH_NAME
    OILWATCH_EMAIL
    OILWATCH_PHONE
    OILWATCH_POSTCODE
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from oilwatch.config import load_settings

ENV_KEYS = {
    "name": "OILWATCH_NAME",
    "email": "OILWATCH_EMAIL",
    "phone": "OILWATCH_PHONE",
    "postcode": "OILWATCH_POSTCODE",
}


@dataclass(frozen=True, slots=True)
class Contact:
    """The delivery identity. Fields are empty when nothing is configured."""

    name: str = ""
    email: str = ""
    phone: str = ""
    postcode: str = ""

    @property
    def is_complete(self) -> bool:
        """True when there is enough detail to submit a supplier enquiry form."""
        return bool(self.name and self.email and self.postcode)


def _settings_postcode(base: Path) -> str:
    """The delivery postcode from ``config/settings.json``.

    Resolved through the same parser as the rest of the settings rather than a
    second read of the file; absent or malformed settings simply mean there is
    no fallback postcode.
    """
    try:
        return load_settings(base).default_postcode.strip()
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def _resolve(root: Path | None) -> Contact:
    base = root or Path.cwd()
    from_file: dict[str, str] = {}
    path = base / "config" / "contact.json"
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                from_file = {k: str(v) for k, v in loaded.items() if isinstance(v, (str, int))}
        except (json.JSONDecodeError, OSError):
            from_file = {}

    def pick(field: str, fallback: str = "") -> str:
        return (os.environ.get(ENV_KEYS[field]) or from_file.get(field) or fallback).strip()

    return Contact(
        name=pick("name"),
        email=pick("email"),
        phone=pick("phone"),
        postcode=pick("postcode", _settings_postcode(base)),
    )


_cached: Contact | None = None


def load_contact(root: Path | None = None, *, refresh: bool = False) -> Contact:
    """Return the configured :class:`Contact`.

    Cached, because connectors ask for it per quote. Pass ``root`` to read a
    specific checkout (tests do), or ``refresh=True`` to re-read.
    """
    global _cached
    if root is not None or refresh or _cached is None:
        resolved = _resolve(root)
        if root is None:
            _cached = resolved
        return resolved
    return _cached
