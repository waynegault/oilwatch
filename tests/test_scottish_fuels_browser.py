"""The Scottish Fuels browser connector (Selenium), driven by a fake driver.

BrowserAuth and the sleeps are patched out; everything else runs for real,
including the fuel-type lookup, which is read through the fake driver's radios
rather than stubbed. The point is the session/quote logic that decides between
an ok quote and each manual fallback.
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


class FakeRadio:
    """A ``productSelection`` radio, carrying the label its wrapper renders."""

    def __init__(self, value: str, label: str = "", *, label_lookup_fails: bool = False) -> None:
        self.value = value
        self.label = label
        self.label_lookup_fails = label_lookup_fails

    def get_attribute(self, name: str):
        return self.value if name == "value" else None


class FakeDriver:
    """Just enough Selenium: navigation, the form controls, and the page text."""

    def __init__(
        self,
        *,
        body_text: str = "",
        redirect_to: str | None = None,
        redirect_gets: int | None = None,
        current_url: str = QUOTE_URL,
        radios: list[FakeRadio] | None = None,
        after_quote_url: str | None = None,
        fail_get: bool = False,
    ) -> None:
        self._body_text = body_text
        self._redirect_to = redirect_to
        # None redirects every load; a count lets the sign-in and the second
        # /quote/ load behave differently, which is what the retry path needs.
        self._redirect_gets = redirect_gets
        self._after_quote_url = after_quote_url
        self._fail_get = fail_get
        self.current_url = current_url
        self.urls: list[str] = []
        self.scripts: list[str] = []
        self.gets = 0
        self.quantity = FakeWebElement()
        self.radios = radios if radios is not None else [FakeRadio("3302", "Kerosene 28s")]

    def get(self, url: str) -> None:
        if self._fail_get:
            raise RuntimeError("no such window: target window already closed")
        # A redirect target models the 302 the quote page answers with when there
        # is no live session.
        self.urls.append(url)
        self.gets += 1
        redirecting = self._redirect_to and (
            self._redirect_gets is None or self.gets <= self._redirect_gets
        )
        self.current_url = self._redirect_to if redirecting else url

    def execute_script(self, script: str, *args):
        self.scripts.append(script)
        if args and "closest" in script:  # the label lookup
            radio = args[0]
            if radio.label_lookup_fails:
                raise RuntimeError("stale element reference")
            return radio.label
        if args and "click" in script and self._after_quote_url:
            # Submitting the quote is what bounces a dead session to sign-in.
            self.current_url = self._after_quote_url
        return None

    def find_element(self, by: str, value: str):
        if by == "tag name" and value == "body":
            return FakeWebElement(text=self._body_text)
        if by == "css selector" and "quantity" in value:
            return self.quantity
        return FakeWebElement()

    def find_elements(self, by: str, value: str) -> list[FakeRadio]:
        if "productSelection" in value:
            return list(self.radios)
        return []


class ScottishFuelsBrowserConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = ScottishFuelsBrowserConnector()
        self.auth = MagicMock()
        for patcher in (
            patch("oilwatch.browser_auth.BrowserAuth", return_value=self.auth),
            patch("oilwatch.connectors.suppliers.scottish_fuels_browser.time.sleep", return_value=None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _quote(self, driver: FakeDriver):
        self.auth.launch.return_value = driver
        return self.connector.quote(SUPPLIER, 1000, {"postcode": "AB21 0YA"})

    def _expired_session(self, **driver_kwargs) -> FakeDriver:
        """A driver whose /quote/ load bounces to the sign-in screen."""
        creds = patch(
            "oilwatch.connectors.suppliers.scottish_fuels_browser.get_supplier_credentials",
            return_value={"email": "owner@example.test", "password": "hunter2"},
        )
        creds.start()
        self.addCleanup(creds.stop)
        return FakeDriver(redirect_to=self.connector.login_url, **driver_kwargs)

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

    def test_the_fuel_type_comes_from_the_form(self) -> None:
        driver = FakeDriver(
            body_text="Your quote\n101.03p (Excl. VAT)\nLogout",
            radios=[
                FakeRadio("3302", "Kerosene 28s"),
                FakeRadio("418", "Heating Oil"),
                FakeRadio("", "unlabelled"),
            ],
        )

        result = self._quote(driver)

        self.assertEqual(result.status, "ok")
        self.assertEqual(
            result.raw_payload["product_options"], {"3302": "Kerosene 28s", "418": "Heating Oil"}
        )
        self.assertEqual(result.raw_payload["product_sku"], "3302")

    def test_a_radio_whose_label_will_not_load_is_still_selectable(self) -> None:
        """A label lookup that fails must not cost the owner the quote."""
        driver = FakeDriver(
            body_text="Your quote\n101.03p (Excl. VAT)\nLogout",
            radios=[FakeRadio("451", label_lookup_fails=True)],
        )

        result = self._quote(driver)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.raw_payload["product_options"], {"451": ""})
        self.assertEqual(result.raw_payload["product_sku"], "451")

    def test_a_form_with_no_fuel_type_options_is_manual(self) -> None:
        driver = FakeDriver(body_text="Your quote\n101.03p (Excl. VAT)\nLogout", radios=[])

        result = self._quote(driver)

        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("No fuel-type options", result.notes)

    def test_an_expired_session_is_signed_in_again_and_the_quote_continues(self) -> None:
        driver = self._expired_session(
            body_text="Your quote\n101.03p (Excl. VAT)\nLogout", redirect_gets=1
        )
        self.auth.sign_in.return_value = True

        result = self._quote(driver)

        self.assertEqual(result.status, "ok")
        # /quote/ was loaded, bounced to the login screen, then loaded again.
        self.assertEqual(driver.urls, [QUOTE_URL, QUOTE_URL])
        self.auth.sign_in.assert_called_once_with(
            driver, self.connector.login_url, "owner@example.test", "hunter2"
        )
        self.auth.close.assert_called_once()

    def test_a_sign_in_that_does_not_take_is_manual(self) -> None:
        driver = self._expired_session(redirect_gets=1)
        self.auth.sign_in.return_value = False

        result = self._quote(driver)

        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("did not take", result.notes)
        self.assertIn("oilwatch login scottish_fuels", result.notes)

    def test_a_sign_in_that_raises_is_manual(self) -> None:
        driver = self._expired_session(redirect_gets=1)
        self.auth.sign_in.side_effect = RuntimeError("reCAPTCHA challenge")

        result = self._quote(driver)

        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("automatic sign-in failed", result.notes)
        self.assertIn("reCAPTCHA challenge", result.notes)

    def test_a_session_that_expires_again_after_signing_in_is_manual(self) -> None:
        driver = self._expired_session(redirect_gets=2)
        self.auth.sign_in.return_value = True

        result = self._quote(driver)

        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("still redirects to the account page", result.notes)

    def test_a_session_that_expires_mid_quote_is_manual(self) -> None:
        driver = FakeDriver(
            body_text="Your quote\n101.03p (Excl. VAT)\nLogout",
            after_quote_url=self.connector.login_url,
        )

        result = self._quote(driver)

        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("Session expired mid-quote", result.notes)

    def test_a_driver_that_breaks_is_reported_and_the_browser_is_closed(self) -> None:
        driver = FakeDriver(fail_get=True)

        result = self._quote(driver)

        self.assertEqual(result.status, "manual_action_required")
        self.assertIn("Browser automation error", result.notes)
        self.auth.close.assert_called_once()

if __name__ == "__main__":
    unittest.main()
