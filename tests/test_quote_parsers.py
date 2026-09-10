"""Parser tests for the supplier connectors with a separable price extraction."""

from __future__ import annotations

import unittest

from oilwatch.connectors.suppliers.highland_fuels import HighlandFuelsConnector
from oilwatch.connectors.suppliers.scottish_fuels_browser import ScottishFuelsBrowserConnector


class HighlandOffersParsingTests(unittest.TestCase):
    def test_reads_the_cheapest_unit_price_as_pence(self) -> None:
        xml = (
            "<Offers>"
            "<Offer><UnitPrice>104.50</UnitPrice></Offer>"
            "<Offer><UnitPrice>101.03</UnitPrice></Offer>"
            "</Offers>"
        )
        self.assertEqual(HighlandFuelsConnector.parse_offers_response(xml), 1.0103)

    def test_offers_without_a_price_are_none(self) -> None:
        self.assertIsNone(HighlandFuelsConnector.parse_offers_response("<Offers></Offers>"))

    def test_malformed_xml_is_none(self) -> None:
        self.assertIsNone(HighlandFuelsConnector.parse_offers_response("not xml"))


class ScottishFuelsParsingTests(unittest.TestCase):
    def test_reads_the_cheapest_ppl_across_both_wording_forms(self) -> None:
        text = "Option A: 101.03p per litre (Excl. VAT)\nOption B: 99.50p (Excl. VAT)"
        self.assertEqual(ScottishFuelsBrowserConnector.parse_ppl(text), 0.995)

    def test_no_price_is_none(self) -> None:
        self.assertIsNone(ScottishFuelsBrowserConnector.parse_ppl("no prices here"))

    def test_is_logged_in_detects_the_markers(self) -> None:
        self.assertTrue(ScottishFuelsBrowserConnector.is_logged_in("Welcome back, Wayne"))
        self.assertTrue(ScottishFuelsBrowserConnector.is_logged_in("Logout"))
        self.assertFalse(ScottishFuelsBrowserConnector.is_logged_in("Please sign in"))


if __name__ == "__main__":
    unittest.main()
