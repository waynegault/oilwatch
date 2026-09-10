"""DiscoveryService's search, enrichment and filtering loop.

The HTTP client and the geocoder are faked, so discover() runs end to end
without the network.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from oilwatch.config import HomeConfig, Settings
from oilwatch.discovery import DiscoveryService

SEARCH_HTML = """
<div class="result">
  <a class="result__a" href="https://www.turriff-fuels.co.uk/">Heating Oil Prices | Turriff Fuels</a>
  <a class="result__snippet">Cheap heating oil in Aberdeenshire</a>
</div>
"""

SITE_HTML = """
<html><body>
  <p>Turriff Fuels, Aberdeenshire AB51 3AB</p>
  <a href="mailto:info@turriff-fuels.co.uk">Email us</a>
  <p>Call 01888 562706</p>
</body></html>
"""


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class FakeHttpClient:
    """Returns canned HTML by URL substring; can fail per URL."""

    def __init__(self, responses: dict[str, str], *, fail: tuple[str, ...] = ()) -> None:
        self._responses = responses
        self._fail = fail
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url: str, params: dict | None = None) -> FakeResponse:
        self.calls.append((url, params))
        if any(marker in url for marker in self._fail):
            raise RuntimeError("connection reset")
        for marker, text in self._responses.items():
            if marker in url:
                return FakeResponse(text)
        return FakeResponse("")


class FakeGeo:
    def __init__(self, coordinates: tuple[float, float] | None = (57.5, -2.4)) -> None:
        self.coordinates = coordinates

    def geocode(self, query: str):
        return (self.coordinates[0], self.coordinates[1], "Aberdeenshire") if self.coordinates else None

    def geocode_first(self, queries):
        for query in queries:
            if query:
                return self.geocode(query)
        return None

    def distance_miles(self, a, b) -> float:
        return 5.0


def _settings(**overrides: Any) -> Settings:
    base = dict(
        database_path=Path("data/oilwatch.sqlite"),
        chart_path=Path("data/chart.png"),
        time_series_chart_path=Path("data/ts.png"),
        home=HomeConfig(label="Hatton of Fintry, Aberdeenshire", latitude=57.2, longitude=-2.2),
        search_queries=["heating oil aberdeen"],
    )
    base.update(overrides)
    return Settings(**base)


def _service(client: FakeHttpClient, geo: FakeGeo | None = None, **overrides: Any) -> DiscoveryService:
    service = DiscoveryService(_settings(**overrides), geo or FakeGeo())
    service.client = client
    return service


class SearchTests(unittest.TestCase):
    def test_search_returns_the_page_text(self) -> None:
        client = FakeHttpClient({"html.duckduckgo.com": "<html>results</html>"})
        self.assertEqual(_service(client)._search("heating oil aberdeen"), "<html>results</html>")
        url, params = client.calls[0]
        self.assertIn("duckduckgo", url)
        self.assertEqual(params["q"], "heating oil aberdeen")


class DiscoverTests(unittest.TestCase):
    def test_a_nearby_result_is_enriched_and_activated(self) -> None:
        client = FakeHttpClient({"html.duckduckgo.com": SEARCH_HTML, "turriff-fuels.co.uk": SITE_HTML})
        results = _service(client).discover()

        self.assertEqual(len(results), 1)
        candidate = results[0]
        self.assertEqual(candidate.status, "active")
        self.assertEqual(candidate.distance_miles, 5.0)
        self.assertEqual(candidate.email, "info@turriff-fuels.co.uk")
        self.assertEqual(candidate.phone, "01888 562706")
        self.assertIn("AB51 3AB", candidate.address)

    def test_an_ungeocodable_result_still_counts_when_it_looks_local(self) -> None:
        client = FakeHttpClient({"html.duckduckgo.com": SEARCH_HTML, "turriff-fuels.co.uk": SITE_HTML})
        results = _service(client, FakeGeo(coordinates=None)).discover()

        self.assertEqual(len(results), 1)
        self.assertIn("Distance not geocoded", results[0].notes)

    def test_a_result_beyond_the_radius_is_dropped(self) -> None:
        class FarGeo(FakeGeo):
            def distance_miles(self, a, b) -> float:
                return 500.0

        client = FakeHttpClient({"html.duckduckgo.com": SEARCH_HTML, "turriff-fuels.co.uk": SITE_HTML})
        self.assertEqual(_service(client, FarGeo()).discover(), [])

    def test_an_enrichment_failure_is_noted_but_still_geocodes(self) -> None:
        client = FakeHttpClient({"html.duckduckgo.com": SEARCH_HTML}, fail=("turriff-fuels.co.uk",))
        results = _service(client).discover()

        self.assertEqual(len(results), 1)
        self.assertIn("Enrichment limited", results[0].notes)


if __name__ == "__main__":
    unittest.main()
