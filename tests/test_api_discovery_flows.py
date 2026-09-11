"""APIDiscoveryTool's orchestration and output.

_setup is stubbed with a fake page, so discover(), the summary grouping and the
result file are exercised without a browser.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from oilwatch.api_discovery import APIDiscoveryTool, discover_supplier_api
from tests.fake_async_page import FakeAsyncPage, FakeAsyncPlaywright, FakeBrowser, FakeContext

URL = "https://example.co.uk"


class FakePlaywright:
    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


def _tool(page: FakeAsyncPage) -> tuple[APIDiscoveryTool, FakePlaywright]:
    tool = APIDiscoveryTool()
    tool._page = page
    tool._playwright = FakePlaywright()
    return tool, tool._playwright


class DiscoverTests(unittest.TestCase):
    def test_discover_visits_the_page_and_summarises(self) -> None:
        page = FakeAsyncPage()
        tool, playwright = _tool(page)

        with patch.object(APIDiscoveryTool, "_setup", new=AsyncMock()):
            summary = asyncio.run(tool.discover(URL, timeout=2000))

        self.assertEqual(page.goto_urls, [URL])
        self.assertIn(2000, page.waits)
        self.assertEqual(summary["total_requests"], 0)
        self.assertEqual(summary["api_endpoints_found"], 0)
        # finally-block tear-down
        self.assertTrue(page.context.closed)
        self.assertTrue(playwright.stopped)
        self.assertIsNone(tool._page)

    def test_actions_run_and_a_failing_action_does_not_stop_the_rest(self) -> None:
        page = FakeAsyncPage()
        tool, _ = _tool(page)
        ran: list[str] = []

        async def good(_page):
            ran.append("good")

        async def bad(_page):
            raise RuntimeError("action blew up")

        with (
            patch.object(APIDiscoveryTool, "_setup", new=AsyncMock()),
            patch("builtins.print"),
        ):
            asyncio.run(tool.discover(URL, actions=[bad, good]))

        self.assertEqual(ran, ["good"])

    def test_get_summary_groups_endpoints_by_base_path(self) -> None:
        tool = APIDiscoveryTool()
        deep = "https://example.co.uk/api/v1/quotes/123/prices"
        tool._api_endpoints = {deep: {}, "https://example.co.uk/api/other": {}}

        summary = tool.get_summary()

        self.assertEqual(summary["api_endpoints_found"], 2)
        self.assertTrue(any(deep in urls for urls in summary["endpoint_groups"].values()))

    def test_save_results_writes_the_summary_and_traffic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = APIDiscoveryTool(output_dir=Path(tmp))
            tool._requests = [{"url": f"{URL}/api/quote", "method": "GET", "headers": {}, "post_data": None, "resource_type": "xhr"}]

            path = tool.save_results("discovery.json")
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["summary"]["total_requests"], 1)
        self.assertEqual(payload["all_requests"], tool._requests)


class ConvenienceFunctionTests(unittest.TestCase):
    def test_discover_supplier_api_without_an_output_file_does_not_save(self) -> None:
        with (
            patch.object(APIDiscoveryTool, "discover", new=AsyncMock(return_value={"total_requests": 0})),
            patch.object(APIDiscoveryTool, "save_results") as save,
        ):
            result = asyncio.run(discover_supplier_api(URL))

        self.assertEqual(result, {"total_requests": 0})
        save.assert_not_called()

    def test_discover_supplier_api_saves_when_asked(self) -> None:
        with (
            patch.object(APIDiscoveryTool, "discover", new=AsyncMock(return_value={})),
            patch.object(APIDiscoveryTool, "save_results", return_value=Path("/tmp/x.json")) as save,
            patch("builtins.print"),
        ):
            asyncio.run(discover_supplier_api(URL, output_file="x.json"))

        save.assert_called_once_with("x.json")


class SetupTests(unittest.TestCase):
    """The browser launch itself, which the flow tests patch out.

    That patch is exactly why this test exists: ``_setup`` is the playwright
    sequence every discovery run begins with, and stubbing it meant nothing ever
    ran it.
    """

    def test_setup_launches_a_browser_and_intercepts_every_request(self) -> None:
        page = FakeAsyncPage()
        context = FakeContext(page)
        playwright = FakeAsyncPlaywright(FakeBrowser(context))

        with patch("oilwatch.api_discovery.async_playwright", return_value=playwright):
            tool = APIDiscoveryTool()
            returned = asyncio.run(tool._setup(headless=False))

        self.assertIs(returned, page)
        self.assertIs(tool._page, page)
        # Everything is routed through the interceptor, which is the point of it.
        # (assertEqual, not assertIs: a bound method is a new object each time.)
        self.assertEqual(context.route_handler, tool._intercept)
        self.assertIs(context.route_handler.__self__, tool)


if __name__ == "__main__":
    unittest.main()
