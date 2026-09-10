from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from oilwatch.connectors.suppliers.fueltool import FueltoolConnector
from oilwatch.connectors.suppliers.fuelsoft import FuelsoftConnector
from oilwatch.connectors.suppliers.highland_fuels import HighlandFuelsConnector
from oilwatch.connectors.suppliers.rix_browser import RixBrowserConnector
from oilwatch.connectors.suppliers.scottish_fuels_browser import ScottishFuelsBrowserConnector


def fake_response(text: str, url: str = "https://www.fueltool.co.uk/") -> Mock:
    response = Mock()
    response.text = text
    response.url = url
    response.status_code = 200
    response.raise_for_status = Mock()
    return response


class FueltoolConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.supplier = {"id": 1, "name": "Fueltool", "website": "https://www.fueltool.co.uk/"}

    def test_extract_average(self) -> None:
        html = "Fueltool average today: 98.73p per litre (ex VAT, 1,000L)"
        self.assertEqual(FueltoolConnector._extract_average(html), 0.9873)

    def test_quote(self) -> None:
        html = "<html>Fueltool average today: 98.73p per litre (ex VAT, 1,000L)</html>"
        with patch("oilwatch.connectors.suppliers.fueltool.httpx.Client") as Client:
            Client.return_value.get.return_value = fake_response(html)
            result = FueltoolConnector().quote(self.supplier, 1000, {})
        self.assertEqual(result.status, "ok")
        # 98.73p ex-VAT -> £0.9873 -> 5% VAT -> ~£1.0367 inclusive.
        self.assertAlmostEqual(result.price_per_liter, 1.0367, places=4)
        self.assertAlmostEqual(result.total_price, 1036.7, places=1)

    def test_no_price_returns_manual_action(self) -> None:
        with patch("oilwatch.connectors.suppliers.fueltool.httpx.Client") as Client:
            Client.return_value.get.return_value = fake_response("<html>no price</html>")
            result = FueltoolConnector().quote(self.supplier, 1000, {})
        self.assertEqual(result.status, "manual_action_required")


class FuelsoftParseQuoteResponseTests(unittest.TestCase):
    def test_extracts_cheapest_ppl(self) -> None:
        body = [{"PPL": 1.0878}, {"PPL": 1.15}]
        self.assertEqual(FuelsoftConnector.parse_quote_response(body), 1.0878)

    def test_single_quote(self) -> None:
        self.assertEqual(FuelsoftConnector.parse_quote_response([{"PPL": 1.0878}]), 1.0878)

    def test_missing_ppl_returns_none(self) -> None:
        self.assertIsNone(FuelsoftConnector.parse_quote_response([{"Goods": 1087.8}]))
        self.assertIsNone(FuelsoftConnector.parse_quote_response(None))


class RixParsePplTests(unittest.TestCase):
    def test_extracts_cheapest_ppl(self) -> None:
        text = "Economy 10 days PPL (ex. VAT) 110.35p ... Standard 5 days PPL (ex. VAT) 112.35p"
        self.assertEqual(RixBrowserConnector.parse_ppl(text), 1.1035)

    def test_no_ppl_returns_none(self) -> None:
        self.assertIsNone(RixBrowserConnector.parse_ppl("no price here"))


class ScottishFuelsParsePplTests(unittest.TestCase):
    def test_extracts_ppl_ex_vat(self) -> None:
        text = (
            "Premium Kerosene\n1000 litres\n101.03p per litre (Excl. VAT)\n"
            "£1,010.30\nTotal (Excl. VAT)\n£1,010.30"
        )
        self.assertEqual(ScottishFuelsBrowserConnector.parse_ppl(text), 1.0103)

    def test_extracts_ppl_table_form(self) -> None:
        text = (
            "Fuel Type Quantity Price Per Litre Total Excl. VAT VAT 5% Total\n"
            "Premium Kerosene 1000 101.03p (Excl. VAT) £1010.30 £50.52 £1060.82"
        )
        self.assertEqual(ScottishFuelsBrowserConnector.parse_ppl(text), 1.0103)

    def test_no_price_returns_none(self) -> None:
        self.assertIsNone(ScottishFuelsBrowserConnector.parse_ppl("no price here"))

    def test_is_logged_in(self) -> None:
        self.assertTrue(ScottishFuelsBrowserConnector.is_logged_in("Welcome ... Logout (owner@example.com) ... Your latest quote"))
        self.assertFalse(ScottishFuelsBrowserConnector.is_logged_in("CUSTOMER LOGIN Sign In Email or account number"))


class HighlandParseOffersTests(unittest.TestCase):
    def test_extracts_unit_price(self) -> None:
        xml = (
            "<Response><ResultStatus>1</ResultStatus><Offers><Offer>"
            "<Quantity>1000</Quantity><UnitPrice>109.2</UnitPrice><Total>114660</Total>"
            "</Offer></Offers></Response>"
        )
        self.assertEqual(HighlandFuelsConnector.parse_offers_response(xml), 1.092)

    def test_no_offer_returns_none(self) -> None:
        self.assertIsNone(HighlandFuelsConnector.parse_offers_response("<Response><ResultStatus>0</ResultStatus></Response>"))


if __name__ == "__main__":
    unittest.main()
