from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from oilwatch.connectors import get_connector_for_supplier
from oilwatch.connectors.suppliers import get_telephone_script

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

    def test_telephone_script_resolves_through_the_registry(self) -> None:
        script = get_telephone_script()
        self.assertEqual(type(script).__name__, "TelephoneQuoteScript")


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
