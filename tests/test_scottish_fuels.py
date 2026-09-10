from __future__ import annotations

import unittest

from oilwatch.connectors.suppliers.scottish_fuels_browser import ScottishFuelsBrowserConnector

Connector = ScottishFuelsBrowserConnector


class LoginPageDetectionTests(unittest.TestCase):
    """`/quote/` 302s to this path when the customer session is dead.

    The connector used to press on into the sign-in page and die with
    "no such element: input[name='productSelection'][value='451']", which read
    like the supplier had redesigned their form.
    """

    def test_login_redirect_is_detected(self) -> None:
        self.assertTrue(Connector.is_login_page("https://quote.scottishfuels.co.uk/customer/account/login/"))
        self.assertTrue(
            Connector.is_login_page(
                "https://quote.scottishfuels.co.uk/customer/account/login/referer/aHR0cHM6"
            )
        )

    def test_the_signed_in_account_page_is_not_a_login_page(self) -> None:
        """Both the login screen and the dashboard live under /customer/account/.

        Matching that bare prefix reported a working session as unauthenticated.
        """
        self.assertFalse(Connector.is_login_page("https://quote.scottishfuels.co.uk/customer/account/"))

    def test_quote_page_is_not_treated_as_login(self) -> None:
        self.assertFalse(Connector.is_login_page("https://quote.scottishfuels.co.uk/quote/"))

    def test_missing_url_is_not_a_login_page(self) -> None:
        self.assertFalse(Connector.is_login_page(None))
        self.assertFalse(Connector.is_login_page(""))


class ProductSkuChoiceTests(unittest.TestCase):
    """The supplier renumbers the fuel-type radios, so selection must adapt."""

    OPTIONS = {"451": "Premium Kerosene", "418": "Heating Oil", "555": "Diesel"}

    def test_configured_sku_wins_when_present(self) -> None:
        self.assertEqual(Connector.choose_product_sku("418", self.OPTIONS), "418")

    def test_unknown_sku_falls_back_to_a_kerosene_option(self) -> None:
        # 999 no longer exists; 451's label still says kerosene.
        self.assertEqual(Connector.choose_product_sku("999", self.OPTIONS), "451")

    def test_heating_oil_label_is_also_accepted(self) -> None:
        options = {"777": "Heating Oil 28s", "888": "Diesel"}
        self.assertEqual(Connector.choose_product_sku("999", options), "777")

    def test_last_resort_is_the_first_option(self) -> None:
        options = {"111": "Mystery Fuel A", "222": "Mystery Fuel B"}
        self.assertEqual(Connector.choose_product_sku("999", options), "111")

    def test_no_options_returns_none(self) -> None:
        self.assertIsNone(Connector.choose_product_sku("451", {}))


class ParsePriceTests(unittest.TestCase):
    def test_parses_both_known_phrasings(self) -> None:
        self.assertAlmostEqual(Connector.parse_ppl("101.03p per litre (Excl. VAT)"), 1.0103)
        self.assertAlmostEqual(Connector.parse_ppl("101.03p (Excl. VAT)"), 1.0103)

    def test_returns_the_cheapest_of_several(self) -> None:
        text = "104.00p (Excl. VAT) and also 101.03p (Excl. VAT)"
        self.assertAlmostEqual(Connector.parse_ppl(text), 1.0103)

    def test_returns_none_when_no_price_present(self) -> None:
        self.assertIsNone(Connector.parse_ppl("No prices on this page"))


if __name__ == "__main__":
    unittest.main()
