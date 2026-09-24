from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oilwatch.config import load_settings, load_supplier_registry
from oilwatch.connectors import get_supplier_connector
from oilwatch.form_submit import SUPPLIER_FORMS


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "config").mkdir(parents=True, exist_ok=True)
        (self.root / "data").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_settings(self) -> None:
        settings = {
            "database_path": "data/test.sqlite",
            "chart_path": "data/test.png",
            "home": {"label": "Home", "latitude": 57.2, "longitude": -2.2},
            "radius_miles": 50,
            "quote_quantity_liters": 1000,
            "currency": "GBP",
            "search_queries": ["heating oil Aberdeenshire"],
            "scheduler": {"discovery_interval_hours": 168, "quote_interval_hours": 24},
        }
        (self.root / "config" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    def _write_register(
        self, suppliers: list[dict[str, object]] | None = None, excluded: list[str] | None = None
    ) -> None:
        (self.root / "config" / "suppliers.json").write_text(
            json.dumps({"suppliers": suppliers or [], "excluded_domains": excluded or []}),
            encoding="utf-8",
        )

    def test_load_settings(self) -> None:
        self._write_settings()
        settings = load_settings(self.root)
        self.assertEqual(settings.home.label, "Home")
        self.assertEqual(settings.radius_miles, 50)
        self.assertEqual(settings.quote_quantity_liters, 1000)
        # Absent from the file, so the loader's own default applies.
        self.assertEqual(settings.quote_max_workers, 4)
        self.assertEqual(settings.search_queries, ["heating oil Aberdeenshire"])
        self.assertEqual(settings.scheduler.quote_interval_hours, 24)

    def test_the_domain_exclusions_come_from_the_register_not_settings(self) -> None:
        """Supplier policy is version controlled; settings.json is not.

        excluded_domains decides which companies count as suppliers, so it lives
        with the suppliers in the tracked register. settings.json is gitignored
        and holds the owner's personal values and operational settings — address,
        postcode, credentials, the freshness window, the order quantity — but no
        supplier policy, which is the distinction this checks.
        """
        self._write_settings()
        self._write_register(excluded=["example.com", "yell.com"])
        self.assertEqual(load_settings(self.root).excluded_domains, ["example.com", "yell.com"])

    def test_settings_default_to_the_checkout_root(self) -> None:
        """A process spawned elsewhere still reads this checkout's settings.

        The MCP server is launched by another program, so a default of
        ``Path.cwd()`` would read whichever directory it happened to inherit.
        """
        self._write_settings()
        with patch("oilwatch.config.CHECKOUT_ROOT", self.root):
            loaded = load_settings()
        self.assertEqual(loaded.home.label, "Home")

    def test_load_settings_reads_the_login_urls(self) -> None:
        settings = {
            "database_path": "data/test.sqlite",
            "chart_path": "data/test.png",
            "home": {"label": "Home", "latitude": 57.2, "longitude": -2.2},
            "login_urls": {"scottish_fuels": "https://quote.scottishfuels.co.uk/quote/"},
        }
        (self.root / "config" / "settings.json").write_text(
            json.dumps(settings), encoding="utf-8"
        )
        loaded = load_settings(self.root)
        self.assertEqual(
            loaded.login_urls, {"scottish_fuels": "https://quote.scottishfuels.co.uk/quote/"}
        )

    def test_the_login_urls_default_to_empty(self) -> None:
        self._write_settings()
        self.assertEqual(load_settings(self.root).login_urls, {})

    def test_load_supplier_registry_missing_file(self) -> None:
        self.assertEqual(load_supplier_registry(self.root), {"suppliers": [], "excluded_domains": []})

    def test_load_supplier_registry(self) -> None:
        # The register holds arbitrary JSON, so object is the honest value type;
        # a bare literal infers dict[str, str] and pyright rejects the call as an
        # invariance error, which is why this annotation earns its place.
        suppliers: list[dict[str, object]] = [
            {"name": "A", "website": "https://a.example.com"}
        ]
        self._write_register(suppliers=suppliers, excluded=["yell.com"])
        self.assertEqual(
            load_supplier_registry(self.root),
            {"suppliers": suppliers, "excluded_domains": ["yell.com"]},
        )

    def test_a_register_that_is_not_an_object_is_refused(self) -> None:
        """A stale bare list must fail loudly, not read as no suppliers at all.

        Same reasoning as the settings drift check: the file's shape is part of
        its contract, and silently importing nothing looks like an install that
        has simply not been set up yet.
        """
        (self.root / "config" / "suppliers.json").write_text(
            json.dumps([{"name": "A", "website": "https://a.example.com"}]), encoding="utf-8"
        )
        with self.assertRaises(ValueError) as caught:
            load_supplier_registry(self.root)
        self.assertIn("bare list", str(caught.exception))


class ShippedRegisterTests(unittest.TestCase):
    """The register this repo ships has to be actionable, supplier by supplier.

    AGENTS.md calls `config/suppliers.json` the list of who is asked and how, and
    a record can be inert three ways without anything failing at run time: a
    `form` key that is not in `SUPPLIER_FORMS` (`submit-requests` prints
    "unknown" for it and no enquiry happens), a `no_form` record with no address
    (reported as `no_address` on every run, forever), and a record that names
    neither while no connector can price the supplier — never asked, never
    quoted, and not named as a gap either. Read from the checked-in file, not a
    fixture: this is about the register in the repository.
    """

    def setUp(self) -> None:
        self.records = load_supplier_registry()["suppliers"]

    def test_every_form_entry_names_a_form_that_is_actually_driven(self) -> None:
        named = {key for record in self.records for key in [(record.get("quote_request") or {}).get("form")] if key}
        unknown = sorted(named - set(SUPPLIER_FORMS))
        self.assertEqual(
            unknown,
            [],
            "these quote_request forms name no entry in SUPPLIER_FORMS, so the "
            "supplier would be reported as 'unknown' and never asked",
        )

    def test_every_no_form_record_carries_an_address_to_ask(self) -> None:
        """`no_form` says "nothing on the site to drive", not "nothing to do".

        It is the email path's work list, and without an address the supplier is
        left unasked with a line saying so on every run — a half-configured
        record rather than a supplier with no route.
        """
        missing = sorted(
            record["name"]
            for record in self.records
            if (record.get("quote_request") or {}).get("no_form") and not record.get("email")
        )
        self.assertEqual(missing, [], "these records say no_form and carry no email address")

    def test_every_active_record_has_a_route(self) -> None:
        """Asked by form, asked by email, or priceable by a connector — pick one.

        An active record with none of the three is a supplier the app will never
        touch: it is not asked, it is not quoted, and nothing reports it as
        missing. A retired record is exempt — it is a tombstone on purpose.
        """
        routeless = []
        for record in self.records:
            if record.get("status", "active") != "active":
                continue
            request = record.get("quote_request") or {}
            if request.get("form") or (request.get("no_form") and record.get("email")):
                continue
            # The connector is asked for with a browser, because that is the
            # strongest route there is: the HTTP name alone is a contact-only
            # connector for several of these suppliers, and would count as a
            # route only for the suppliers the app cannot price.
            if get_supplier_connector(record["website"], prefer_browser=True) is not None:
                continue
            routeless.append(record["name"])
        self.assertEqual(
            routeless,
            [],
            "these active records are never asked and no connector prices them",
        )

    def test_the_ask_vocabulary_is_only_the_two_the_code_reads(self) -> None:
        """`form` and `no_form` are the whole vocabulary; a typo reads as neither.

        A record saying `"quote_request": {"from": "x"}` is not a form and not a
        no-form, so it falls through every branch — the same shape of silence the
        two tests above exist to catch, one layer up.
        """
        unknown = sorted(
            f"{record['name']}: {sorted(request)}"
            for record in self.records
            if (request := record.get("quote_request"))
            and set(request) - {"form", "no_form"}
        )
        self.assertEqual(unknown, [], "these quote_request records use keys nothing reads")


if __name__ == "__main__":
    unittest.main()
