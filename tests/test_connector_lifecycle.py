"""Connectors must release their HTTP connection pool.

Each connector is built per supplier lookup (:func:`get_connector_for_supplier`),
so a client left open is a pool abandoned on every call. The services own the
connector's lifetime and close it through the context manager.
"""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from oilwatch.connectors.manual import ManualConnector
from oilwatch.connectors.suppliers.valueoils import ValueOilsConnector
from oilwatch.quotes import QuoteService

SUPPLIER = {
    "id": 1,
    "name": "ValueOils",
    "website": "https://www.valueoils.com",
    "phone": "01224 000000",
}


def _fake_response(text: str = "<html>no price</html>") -> Mock:
    response = Mock()
    response.text = text
    response.url = "https://example.com"
    response.status_code = 200
    response.raise_for_status = Mock()
    return response


class ConnectorClientLifecycleTests(unittest.TestCase):
    def test_close_releases_the_http_client(self) -> None:
        with patch("oilwatch.connectors.suppliers.valueoils.httpx.Client") as client:
            connector = ValueOilsConnector()
            connector.close()
        client.return_value.close.assert_called_once()

    def test_context_manager_closes_on_exit(self) -> None:
        with patch("oilwatch.connectors.suppliers.valueoils.httpx.Client") as client:
            with ValueOilsConnector():
                pass
        client.return_value.close.assert_called_once()

    def test_a_connector_without_a_client_closes_cleanly(self) -> None:
        connector = ManualConnector()
        self.assertIsNone(connector.client)
        connector.close()  # must not raise

    def test_quote_service_closes_the_connector(self) -> None:
        with patch("oilwatch.connectors.suppliers.valueoils.httpx.Client") as client:
            client.return_value.get.return_value = _fake_response()
            QuoteService(currency="GBP", home_label="Home").quote_supplier(SUPPLIER, 1000)
        client.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
