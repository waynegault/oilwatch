from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oilwatch import secretstore
from oilwatch.credentials import ENVELOPE_FORMAT, ENVELOPE_KEY, CredentialManager


def fake_protect(data: bytes) -> bytes:
    return b"FAKE:" + data[::-1]


def fake_unprotect(data: bytes) -> bytes:
    if not data.startswith(b"FAKE:"):
        raise OSError("not a fake blob")
    return data[5:][::-1]


class EncryptionAtRestTests(unittest.TestCase):
    """The credential file is encrypted at rest; legacy files still load."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "supplier_credentials.json"
        for patcher in (
            patch("oilwatch.credentials.secretstore.available", return_value=True),
            patch("oilwatch.credentials.secretstore.protect", side_effect=fake_protect),
            patch("oilwatch.credentials.secretstore.unprotect", side_effect=fake_unprotect),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_save_writes_an_encrypted_envelope(self) -> None:
        manager = CredentialManager(self.path)
        manager.set_credentials("scottish_fuels", "hunter2")

        raw = self.path.read_text(encoding="utf-8")
        stored = json.loads(raw)
        self.assertEqual(stored[ENVELOPE_KEY], ENVELOPE_FORMAT)
        self.assertIn("blob", stored)
        self.assertNotIn("hunter2", raw, "the password must not appear in the file")
        self.assertTrue(manager.is_encrypted)

    def test_round_trip_through_the_file(self) -> None:
        CredentialManager(self.path).set_credentials("scottish_fuels", "hunter2")
        reopened = CredentialManager(self.path)
        self.assertEqual(reopened.get_credentials("scottish_fuels")["password"], "hunter2")
        self.assertTrue(reopened.is_encrypted)

    def test_legacy_plaintext_still_loads(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "email": "owner@example.com",
                    "credentials": {"valueoils": {"email": "owner@example.com", "password": "plain"}},
                }
            ),
            encoding="utf-8",
        )
        manager = CredentialManager(self.path)
        self.assertEqual(manager.get_credentials("valueoils")["password"], "plain")
        self.assertFalse(manager.is_encrypted)

    def test_resave_upgrades_a_legacy_file(self) -> None:
        self.path.write_text(json.dumps({"credentials": {"valueoils": {"password": "plain"}}}), encoding="utf-8")
        manager = CredentialManager(self.path)
        self.assertFalse(manager.is_encrypted)

        self.assertTrue(manager.resave())

        reopened = CredentialManager(self.path)
        self.assertTrue(reopened.is_encrypted)
        self.assertEqual(reopened.get_credentials("valueoils")["password"], "plain")

    def test_undecryptable_blob_reports_instead_of_crashing(self) -> None:
        """A file from another machine or Windows account must not crash the CLI."""
        blob = base64.b64encode(b"not-a-fake-blob").decode("ascii")
        self.path.write_text(json.dumps({ENVELOPE_KEY: ENVELOPE_FORMAT, "blob": blob}), encoding="utf-8")

        manager = CredentialManager(self.path)
        self.assertIsNotNone(manager.load_error)
        self.assertIsNone(manager.get_credentials("valueoils"))

    def test_falls_back_to_plaintext_when_dpapi_unavailable(self) -> None:
        with patch("oilwatch.credentials.secretstore.available", return_value=False):
            manager = CredentialManager(self.path)
            manager.set_credentials("valueoils", "plain")
        self.assertFalse(manager.is_encrypted)
        self.assertIn("plain", self.path.read_text(encoding="utf-8"))


class DpapiRoundTripTests(unittest.TestCase):
    @unittest.skipUnless(secretstore.available(), "DPAPI is Windows-only")
    def test_protect_then_unprotect(self) -> None:
        secret = b'{"credentials": {"valueoils": {"password": "hunter2"}}}'
        self.assertEqual(secretstore.unprotect(secretstore.protect(secret)), secret)

    @unittest.skipUnless(secretstore.available(), "DPAPI is Windows-only")
    def test_unprotect_rejects_junk(self) -> None:
        with self.assertRaises(OSError):
            secretstore.unprotect(b"definitely not a dpapi blob")


if __name__ == "__main__":
    unittest.main()
