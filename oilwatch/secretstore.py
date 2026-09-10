"""Encrypt a local file at rest using the OS key store.

Windows DPAPI (``CryptProtectData``) is used rather than a hand-rolled cipher:
the key is derived from the logged-in Windows account, so there is no passphrase
to store and no key file to leak, and it needs no extra dependency — ``ctypes``
reaches ``crypt32`` directly.

The consequence is deliberate and worth stating: the ciphertext can only be
decrypted by the same Windows user on the same machine. Move the file to another
account or machine and it is unreadable. For a single-user desktop tool that is
the right trade-off; it protects against another user on the box, a stolen disk,
or a file that gets copied somewhere it should not be.

:func:`available` reports whether encryption is possible. Callers should fall
back to plain text rather than refusing to run, so that a missing DPAPI never
locks the owner out of their own credentials.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

# Refuse to show any UI prompt; this runs non-interactively.
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def available() -> bool:
    """True when DPAPI can be reached on this platform."""
    return sys.platform == "win32" and hasattr(ctypes, "windll")


def _blob_from_bytes(data: bytes) -> _DataBlob:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _blob_to_bytes(blob: _DataBlob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def protect(data: bytes) -> bytes:
    """Encrypt ``data`` for the current Windows user. Raises ``OSError`` on failure."""
    if not available():
        raise OSError("DPAPI is only available on Windows")
    blob_in = _blob_from_bytes(data)
    blob_out = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        None,  # description
        None,  # optional entropy
        None,  # reserved
        None,  # prompt struct
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    ):
        raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
    return _blob_to_bytes(blob_out)


def unprotect(data: bytes) -> bytes:
    """Decrypt data produced by :func:`protect`. Raises ``OSError`` on failure."""
    if not available():
        raise OSError("DPAPI is only available on Windows")
    blob_in = _blob_from_bytes(data)
    blob_out = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,  # description out
        None,  # optional entropy
        None,  # reserved
        None,  # prompt struct
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    ):
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
    return _blob_to_bytes(blob_out)
