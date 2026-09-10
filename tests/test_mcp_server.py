"""The MCP tool surface.

Each tool is a thin delegation to the app; these tests pin the delegation, so a
renamed method or a changed argument is caught here rather than by an agent at
call time.
"""

from __future__ import annotations

import importlib
import unittest
from unittest.mock import MagicMock, patch


class McpToolDelegationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = importlib.import_module("oilwatch.mcp_server")
        self.app = MagicMock()
        patcher = patch.object(self.mod, "_app", self.app)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_read_only_tools_delegate(self) -> None:
        self.mod.list_suppliers()
        self.app.suppliers.assert_called_once_with(include_inactive=False)

        self.mod.current_prices()
        self.app.current_prices.assert_called_once_with()

        self.mod.cheapest()
        self.app.cheapest.assert_called_once_with()

        self.mod.purchases()
        self.app.purchases.assert_called_once_with()

        self.mod.status()
        self.app.status.assert_called_once_with()

    def test_artifact_tools_return_the_path(self) -> None:
        self.app.chart.return_value = "/tmp/chart.png"
        self.assertEqual(self.mod.chart(), "/tmp/chart.png")
        self.app.chart.assert_called_once_with()

        self.mod.time_series_chart()
        self.app.time_series_chart.assert_called_once_with()

    def test_refresh_prices_asks_for_the_browser(self) -> None:
        self.mod.refresh_prices()
        self.app.quote_all.assert_called_once_with(postcode="AB21 0YA", prefer_browser=True)

    def test_refresh_prices_honours_a_custom_postcode(self) -> None:
        self.mod.refresh_prices(postcode="AB10 1AA")
        self.app.quote_all.assert_called_once_with(postcode="AB10 1AA", prefer_browser=True)

    def test_update_brent_delegates(self) -> None:
        self.mod.update_brent()
        self.app.update_brent.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
