"""The Scottish Fuels browser connector (Selenium), driven by a fake driver.

BrowserAuth and the sleeps are patched out; the point is the session/quote logic
that decides between an ok quote and each manual fallback.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from oilwatch.connectors.suppliers.scottish_fuels_browser import ScottishFuelsBrowserConnector

SUPPLIER = {"id": 3, "name": "Scottish Fuels", "website": "https://scottishfuels.co.uk"}
QUOTE_URL = "https://quote.scottishfuels.co.uk/quote/"


class FakeWebElement:
    def __init__(self, text: str = "", value: str = "") -> None:
        self.text = text
        self._value = value
        self.cleared = 0
        self.sent: list[str] = []

    def clear(self) -> None:
        self.cleared += 1

    def send_keys(self, value: str) -> None:
        self.sent.append(value)

    def get_attribute(self, name: str):
        return self._value if name == "value" else None


class FakeDriver:
    def __init__(
        self, *, body_text: str = "", redirect_to: str | None = None, current_url: str = QUOTE_URL
    ) -> None:
        self._body_text = body_text
        self._redirect_to = redirect_to
        self.current_url = current_url
        self.urls: list[str] = []
        self.scripts: list[str] = []
        self.quantity = FakeWebElement()

    def get(self, url: str) -> None:
        # A redirect target models the 302 the quote page answers with when there
        # is no live session.
        self.urls.append(url)
        self.current_url = self._redirect_to or url

    def execute_script(self, script: str, *args) -> None:
        self.scripts.append(script)

    def find_element(self, by: str, value: str):
        if by == "tag name" and value == "body":
            return FakeWebElement(text=self._body_text)
        if by == "css selector" and "quantity" in value:
            return self.quantity
        return FakeWebElement()


class ScottishFuelsBrowserConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = ScottishFuelsBrowserConnector()
        self.auth = MagicMock()
        for patcher in (
            patch("oilwatch.browser_auth.BrowserAuth", return_value=self.auth),
            patch("oilwatch.connectors.suppliers.scottish_fuels_browser.time.sleep", return_value=None),
            patch.object(
                ScottishFuelsBrowserConnector,
                "product_options",
                return_value={"3302": "Kerosene 28s"},
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _quote(self, driver: FakeDriver):
        self.auth.launch.return_value = driver
        return self.connector.quote(SUPPLIER, 1000, {"postcode": "AB21 0YA"})

    def test_extracts_the_price_and_applies_vat(self) -> None:
        driver = FakeDriver(body_text="Your quote\n101.03p per litre (Excl. VAT)\nLogout")
        result = self._quote(driver)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source, "scottish_fuels_browser")
        # 101.03p ex-VAT -> £1.0103 -> +5% VAT -> £1.0608.
        self.assertAlmostEqual(result.price_per_liter, 1.0608, places=4)
        self.assertEqual(result.raw_payload["product_sku"], "3302")
        self.assertEqual(result.raw_payload["configured_product_sku"], "451")
        self.assertEqual(driver.quantity.sent, ["1000"])
        self.assertEqual(driver.urls[0], QUOTE_URL)
        self.auth.close.assert_called_once()

    def test_logged_out_page_is_manual(self) -> None:
        result = self._quote(FakeDriver(body_text="Please sign in to continue"))
        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("Not signed in.", result.notes)

    def test_missing_price_is_manual_not_fabricated(self) -> None:
        result = self._quote(FakeDriver(body_text="Your quote\nSorry, no price available"))
        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("Could not find a price", result.notes)
        self.assertIsNone(result.price_per_liter)

    def test_a_login_redirect_without_stored_credentials_is_manual(self) -> None:
        driver = FakeDriver(redirect_to=self.connector.login_url)
        with patch(
            "oilwatch.connectors.suppliers.scottish_fuels_browser.get_supplier_credentials",
            return_value={},
        ):
            result = self._quote(driver)

        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("stored credentials", result.notes)


if __name__ == "__main__":
    unittest.main()
