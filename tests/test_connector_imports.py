from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from oilwatch.connectors import get_connector_for_supplier
from oilwatch.connectors import suppliers as supplier_registry

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Generous: the child only imports oilwatch.connectors (1-3s normally). A bound
#: is here so a stalled child fails this test loudly instead of hanging the whole
#: discovery run with no summary and no clue which test was stuck.
SUBPROCESS_TIMEOUT_S = 60


class ConnectorRoutingTests(unittest.TestCase):
    def test_unknown_connector_type_raises(self) -> None:
        with self.assertRaises(ValueError):
            get_connector_for_supplier(
                {
                    "name": "Typo Co",
                    "website": "https://unknown.example",
                    "connector_type": "http-form",
                }
            )

    def test_manual_stays_available_without_a_match(self) -> None:
        connector = get_connector_for_supplier(
            {"name": "Phone Only", "website": "https://unknown.example", "connector_type": "manual"}
        )
        self.assertEqual(type(connector).__name__, "ManualConnector")


class ConnectorRegistryTests(unittest.TestCase):
    """Every name in the routing tables must resolve to a class that exists.

    ``_SUPPLIER_CONNECTORS`` maps a domain to connector *names* and ``_CONNECTORS``
    maps those names to the modules holding them, so a typo in either — or a class
    renamed without its entry — is an ``AttributeError`` raised inside a sweep, at
    the one moment nobody can see it coming. Reading the names out of the tables
    rather than restating them here is the point: a list copied into the test
    would drift exactly as the tables do.
    """

    def test_every_routed_name_resolves_to_a_class(self) -> None:
        for domain, (http_name, browser_name) in supplier_registry._SUPPLIER_CONNECTORS.items():
            for name in (http_name, browser_name):
                if name is None:
                    continue
                with self.subTest(domain=domain, connector=name):
                    resolved = getattr(supplier_registry, name)
                    self.assertIsInstance(resolved, type, f"{name} is not a connector class")

    def test_every_mapped_domain_is_reachable_through_the_router(self) -> None:
        """And the router actually takes them, which is what a caller does."""
        for domain, (http_name, browser_name) in supplier_registry._SUPPLIER_CONNECTORS.items():
            if not (http_name or browser_name):
                continue
            with self.subTest(domain=domain):
                connector = get_connector_for_supplier(
                    {"name": f"Site {domain}", "website": f"https://{domain}"}
                )
                self.assertIsNotNone(connector, f"{domain} routes to nothing")


class ConnectorImportCostTests(unittest.TestCase):
    """The HTTP-only path must not drag in the browser stack (Playwright).

    Checked in a subprocess so a Playwright import elsewhere in the suite cannot
    mask a regression.
    """

    def _run(self, code: str) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [sys.executable, "-c", code],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_S,
                # The caller asserts on the return code, so a non-zero exit is
                # the result under test rather than an error to raise here.
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.fail(
                f"the connector-import subprocess did not finish within "
                f"{SUBPROCESS_TIMEOUT_S}s; it may be blocked, not slow"
            )

    def test_importing_connectors_does_not_import_playwright(self) -> None:
        proc = self._run(
            "import sys, oilwatch.connectors; "
            "raise SystemExit(1 if 'playwright' in sys.modules else 0)"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_http_route_does_not_import_playwright(self) -> None:
        proc = self._run(
            "import sys; "
            "from oilwatch.connectors import get_connector_for_supplier as g; "
            "g({'website': 'https://www.valueoils.com', 'connector_type': 'manual'}); "
            "raise SystemExit(1 if 'playwright' in sys.modules else 0)"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
