"""APIDiscoveryTool: request interception, driven by a fake route.

The browser is not involved; ``_intercept`` is the logic that decides what gets
recorded, so that is what these exercise.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from oilwatch.api_discovery import APIDiscoveryTool


class FakeRequest:
    def __init__(self, url, method="GET", headers=None, post_data=None, resource_type="xhr"):
        self.url = url
        self.method = method
        self.headers = headers or {}
        self.post_data = post_data
        self.resource_type = resource_type


class FakeResponse:
    def __init__(self, text, status=200, content_type="application/json"):
        self._text = text
        self.status = status
        self.headers = {"content-type": content_type}

    async def text(self):
        return self._text


class FakeRoute:
    def __init__(self, request, *, response=None, fail=False):
        self.request = request
        self._response = response
        self._fail = fail
        self.fulfilled = False
        self.aborted = False

    async def fetch(self):
        if self._fail:
            raise RuntimeError("network down")
        return self._response

    async def fulfill(self, response=None):
        self.fulfilled = True

    async def abort(self):
        self.aborted = True


def _tool() -> APIDiscoveryTool:
    tool = APIDiscoveryTool()
    tool._requests = []
    tool._responses = []
    tool._api_endpoints = {}
    return tool


class IsApiRequestTests(unittest.TestCase):
    def test_detects_api_looking_urls(self) -> None:
        tool = _tool()
        for url in (
            "https://x.co.uk/api/quote",
            "https://x.co.uk/wp-json/wc/v3/products",
            "https://x.co.uk/prices.json",
            "https://x.co.uk/get?price=1",
        ):
            with self.subTest(url=url):
                self.assertTrue(tool._is_api_request(url))

    def test_ignores_static_assets(self) -> None:
        self.assertFalse(_tool()._is_api_request("https://x.co.uk/static/logo.png"))


class InterceptTests(unittest.TestCase):
    def setUp(self) -> None:
        # REQUEST_DELAY_SECONDS paces a real run; skip it in tests.
        patcher = patch("oilwatch.api_discovery.REQUEST_DELAY_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_records_a_json_api_response(self) -> None:
        tool = _tool()
        route = FakeRoute(
            FakeRequest("https://x.co.uk/api/quote", method="POST", post_data="q=1"),
            response=FakeResponse('{"PPL": 1.05}'),
        )
        asyncio.run(tool._intercept(route))

        self.assertTrue(route.fulfilled)
        self.assertEqual(len(tool._requests), 1)
        self.assertEqual(tool._responses[0]["url"], "https://x.co.uk/api/quote")
        self.assertEqual(
            tool._api_endpoints["https://x.co.uk/api/quote"]["response_data"], {"PPL": 1.05}
        )

    def test_non_api_url_is_recorded_but_not_captured(self) -> None:
        tool = _tool()
        route = FakeRoute(
            FakeRequest("https://x.co.uk/logo.png", resource_type="image"),
            response=FakeResponse("<png>", content_type="image/png"),
        )
        asyncio.run(tool._intercept(route))

        self.assertEqual(len(tool._requests), 1)
        self.assertEqual(tool._responses, [])
        self.assertEqual(tool._api_endpoints, {})

    def test_a_failed_fetch_aborts_the_route(self) -> None:
        tool = _tool()
        route = FakeRoute(FakeRequest("https://x.co.uk/api/quote"), fail=True)
        asyncio.run(tool._intercept(route))

        self.assertTrue(route.aborted)
        self.assertFalse(route.fulfilled)


if __name__ == "__main__":
    unittest.main()
