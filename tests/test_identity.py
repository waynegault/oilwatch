from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oilwatch.identity import Contact, load_contact


class LoadContactTests(unittest.TestCase):
    """Identity is configuration or environment — never a literal in source."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "config").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write(self, name: str, payload: dict) -> None:
        (self.root / "config" / name).write_text(json.dumps(payload), encoding="utf-8")

    def _write_settings(self, **extra: object) -> None:
        """A complete settings.json, since identity now resolves it via Settings."""
        payload: dict[str, object] = {
            "database_path": "data/oilwatch.sqlite",
            "chart_path": "data/oilwatch-market.png",
            "home": {"label": "Home", "latitude": 57.2, "longitude": -2.2},
        }
        payload.update(extra)
        self._write("settings.json", payload)

    def test_empty_when_nothing_configured(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            contact = load_contact(self.root)
        self.assertEqual(contact, Contact())
        self.assertFalse(contact.is_complete)

    def test_reads_contact_json(self) -> None:
        self._write("contact.json", {"name": "A Person", "email": "a@example.com", "phone": "0123"})
        with patch.dict(os.environ, {}, clear=True):
            contact = load_contact(self.root)
        self.assertEqual(contact.name, "A Person")
        self.assertEqual(contact.email, "a@example.com")
        self.assertEqual(contact.phone, "0123")

    def test_environment_wins_over_file(self) -> None:
        self._write("contact.json", {"email": "file@example.com"})
        with patch.dict(os.environ, {"OILWATCH_EMAIL": "env@example.com"}, clear=True):
            contact = load_contact(self.root)
        self.assertEqual(contact.email, "env@example.com")

    def test_postcode_falls_back_to_settings(self) -> None:
        self._write_settings(default_postcode="ZZ99 9ZZ")
        with patch.dict(os.environ, {}, clear=True):
            contact = load_contact(self.root)
        self.assertEqual(contact.postcode, "ZZ99 9ZZ")

    def test_contact_json_postcode_wins_over_settings(self) -> None:
        self._write_settings(default_postcode="ZZ99 9ZZ")
        self._write("contact.json", {"postcode": "AA11 1AA"})
        with patch.dict(os.environ, {}, clear=True):
            contact = load_contact(self.root)
        self.assertEqual(contact.postcode, "AA11 1AA")

    def test_malformed_json_is_not_fatal(self) -> None:
        (self.root / "config" / "contact.json").write_text("{not json", encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True):
            contact = load_contact(self.root)
        self.assertEqual(contact, Contact())


class NoHardcodedIdentityTests(unittest.TestCase):
    def test_package_contains_no_personal_identity(self) -> None:
        """Guard the leak from coming back: identity belongs in config, not code.

        `waynegault@msn.com` and the owner's name used to be defaults across
        ~10 modules, which published them with the repository.
        """
        package = Path(__file__).resolve().parents[1] / "oilwatch"
        needles = ("waynegault@msn.com", "Wayne Gault")
        offenders = sorted(
            str(path.relative_to(package.parent))
            for path in package.rglob("*.py")
            if any(needle in path.read_text(encoding="utf-8") for needle in needles)
        )
        self.assertEqual(offenders, [], f"hardcoded personal identity in: {offenders}")


if __name__ == "__main__":
    unittest.main()
