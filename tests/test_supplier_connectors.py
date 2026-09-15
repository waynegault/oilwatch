from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import httpx

from oilwatch.connectors.suppliers import get_supplier_connector
from oilwatch.connectors.suppliers.fuelsoft import FuelsoftConnector
from oilwatch.connectors.suppliers.homefuels_direct import HomeFuelsDirectConnector
from oilwatch.connectors.suppliers.regency_oils import RegencyOilsConnector
from oilwatch.connectors.suppliers.scottish_fuels_browser import ScottishFuelsBrowserConnector
from oilwatch.connectors.suppliers.valueoils import ValueOilsConnector
from oilwatch.connectors.suppliers.valueoils_browser import ValueOilsBrowserConnector


def fake_response(text: str, url: str = "https://example.com", status_code: int = 200) -> Mock:
    response = Mock()
    response.text = text
    response.url = url
    response.status_code = status_code
    response.raise_for_status = Mock()
    return response


VALUEOILS_PAGE = """
<h3>Live Heating Oil Prices in Aberdeenshire</h3>
<table border="0" class="location-price-table">
  <tr><td>&nbsp;</td><td><strong>500 Litres</strong></td><td><strong>900 Litres</strong></td></tr>
  <tr><td><strong>Price Per Litre Ex VAT</strong></td><td align="right">103.90p</td><td align="right">103.90p</td></tr>
  <tr><td><strong>Total You Pay</strong></td><td align="right">£561.48</td><td align="right">£997.86</td></tr>
</table>
<h3>Live Gas Oil Prices in Aberdeenshire</h3>
<table border="0" class="location-price-table">
  <tr><td><strong>Price Per Litre Ex VAT</strong></td><td align="right">119.70p</td><td align="right">119.70p</td></tr>
</table>
"""

HOMEFUELS_PAGE = (
    '<h3>Our live average price:<br> <span id="currentLivePrice">99.15</span> pence / litre </h3>'
)


class ValueOilsConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.supplier = {"id": 1, "name": "ValueOils", "website": "https://www.valueoils.com"}

    def test_quote_reads_the_900l_total_you_pay(self) -> None:
        """The Total You Pay includes VAT and commission; the ppl excludes both.

        So it is the 900L total that is divided by the tier, not the ppl that is
        uplifted by 5% — the old reading dropped ValueOils' commission.
        """
        with patch("oilwatch.connectors.suppliers.valueoils.httpx.Client") as Client:
            Client.return_value.get.return_value = fake_response(VALUEOILS_PAGE)
            result = ValueOilsConnector().quote(self.supplier, 1000, {"postcode": "AB21 0YA"})

        self.assertEqual(result.status, "ok")
        # £997.86 for 900L inc VAT + commission -> £1.1087/L.
        self.assertEqual(result.raw_payload["tier_total"], 997.86)
        price_per_liter = result.price_per_liter
        assert price_per_liter is not None
        self.assertAlmostEqual(price_per_liter, 1.1087, places=4)
        total_price = result.total_price
        assert total_price is not None
        self.assertAlmostEqual(total_price, 1108.7, places=2)

    def test_no_price_returns_manual_action(self) -> None:
        with patch("oilwatch.connectors.suppliers.valueoils.httpx.Client") as Client:
            Client.return_value.get.return_value = fake_response("<html>no price</html>")
            result = ValueOilsConnector().quote(self.supplier, 1000, {})
        self.assertEqual(result.status, "manual_action_required")

    def test_a_transport_failure_is_reported_as_an_error(self) -> None:
        """A site that will not answer is an error, not a missing price."""
        with patch("oilwatch.connectors.suppliers.valueoils.httpx.Client") as Client:
            Client.return_value.get.side_effect = httpx.ConnectError("connection reset")
            result = ValueOilsConnector().quote(self.supplier, 1000, {})

        self.assertEqual(result.status, "error")
        self.assertIn("HTTP error fetching quote", result.notes)
        self.assertIn("connection reset", result.notes)

    def test_an_unexpected_failure_is_reported_as_an_error(self) -> None:
        with patch("oilwatch.connectors.suppliers.valueoils.httpx.Client") as Client:
            Client.return_value.get.side_effect = RuntimeError("parser blew up")
            result = ValueOilsConnector().quote(self.supplier, 1000, {})

        self.assertEqual(result.status, "error")
        self.assertIn("Error fetching quote", result.notes)


class HomeFuelsDirectConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.supplier = {"id": 2, "name": "HomeFuels Direct", "website": "https://homefuelsdirect.co.uk"}

    def test_quote_extracts_pence_from_live_price_span(self) -> None:
        with patch("oilwatch.connectors.suppliers.homefuels_direct.httpx.Client") as Client:
            Client.return_value.get.return_value = fake_response(HOMEFUELS_PAGE)
            result = HomeFuelsDirectConnector().quote(self.supplier, 1000, {})

        self.assertEqual(result.status, "ok")
        # 99.15 pence ex-VAT -> £0.9915 -> 5% VAT -> ~£1.0411 inclusive.
        price_per_liter = result.price_per_liter
        assert price_per_liter is not None
        self.assertAlmostEqual(price_per_liter, 1.0411, places=4)
        total_price = result.total_price
        assert total_price is not None
        self.assertAlmostEqual(total_price, 1041.1, places=1)

    def test_no_parseable_price_returns_manual_action_not_fabricated(self) -> None:
        with patch("oilwatch.connectors.suppliers.homefuels_direct.httpx.Client") as Client:
            Client.return_value.get.return_value = fake_response("<html>no price here</html>")
            result = HomeFuelsDirectConnector().quote(self.supplier, 1000, {})
        self.assertEqual(result.status, "manual_action_required")
        self.assertIsNone(result.price_per_liter)

    def test_a_transport_failure_is_reported_as_an_error(self) -> None:
        with patch("oilwatch.connectors.suppliers.homefuels_direct.httpx.Client") as Client:
            Client.return_value.get.side_effect = httpx.ConnectError("connection reset")
            result = HomeFuelsDirectConnector().quote(self.supplier, 1000, {})

        self.assertEqual(result.status, "error")
        self.assertIn("HTTP error fetching quote", result.notes)
        self.assertIn("connection reset", result.notes)

    def test_a_failed_first_page_still_tries_the_second(self) -> None:
        """A hiccup on the regional page must not cost the quote."""
        with patch("oilwatch.connectors.suppliers.homefuels_direct.httpx.Client") as Client:
            Client.return_value.get.side_effect = [
                httpx.ConnectError("regional page down"),
                fake_response(HOMEFUELS_PAGE),
            ]
            result = HomeFuelsDirectConnector().quote(self.supplier, 1000, {})

        self.assertEqual(result.status, "ok")
        price_per_liter = result.price_per_liter
        assert price_per_liter is not None
        self.assertAlmostEqual(price_per_liter, 1.0411, places=4)

    def test_an_unexpected_failure_is_reported_as_an_error(self) -> None:
        with patch("oilwatch.connectors.suppliers.homefuels_direct.httpx.Client") as Client:
            Client.return_value.get.side_effect = RuntimeError("parser blew up")
            result = HomeFuelsDirectConnector().quote(self.supplier, 1000, {})

        self.assertEqual(result.status, "error")
        self.assertIn("Error fetching quote", result.notes)


class SupplierConnectorRoutingTests(unittest.TestCase):
    def test_valueoils_uses_the_browser_when_preferred(self) -> None:
        """The browser reads the Quick Quote Standard total; HTTP cannot reach it.

        ValueOils used to be HTTP-wins because its browser connector stalled and
        its form selectors never matched; both are fixed, so --browser now picks
        the connector that can see the delivered (inc-commission) figure.
        """
        self.assertIsInstance(
            get_supplier_connector("https://www.valueoils.com", prefer_browser=True),
            ValueOilsBrowserConnector,
        )

    def test_homefuels_prefers_http_even_with_browser(self) -> None:
        self.assertIsInstance(
            get_supplier_connector("https://homefuelsdirect.co.uk", prefer_browser=True),
            HomeFuelsDirectConnector,
        )

    def test_scottish_fuels_still_uses_browser_connector(self) -> None:
        self.assertIsInstance(
            get_supplier_connector("https://scottishfuels.co.uk", prefer_browser=True),
            ScottishFuelsBrowserConnector,
        )

    def test_default_uses_http(self) -> None:
        self.assertIsInstance(
            get_supplier_connector("https://www.valueoils.com", prefer_browser=False),
            ValueOilsConnector,
        )

    def test_browser_only_domain_needs_prefer_browser(self) -> None:
        # Fuelsoft (Connon Bros / Johnson Oils) has no HTTP connector, so without
        # --browser there is nothing to return and the caller quotes manually.
        self.assertIsNone(get_supplier_connector("https://connon.fuelsoft.co.uk/"))
        self.assertIsInstance(
            get_supplier_connector("https://connon.fuelsoft.co.uk/", prefer_browser=True),
            FuelsoftConnector,
        )

    def test_regency_prefers_browser_when_requested(self) -> None:
        self.assertIsInstance(
            get_supplier_connector("https://www.regencyoils.com/"), RegencyOilsConnector
        )
        self.assertIsInstance(
            get_supplier_connector("https://www.regencyoils.com/", prefer_browser=True),
            FuelsoftConnector,
        )

    def test_unknown_domain_returns_none(self) -> None:
        self.assertIsNone(get_supplier_connector("https://example.invalid"))


if __name__ == "__main__":
    unittest.main()
