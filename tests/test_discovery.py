"""DiscoveryService: the search-result parsing and helpers, with no network."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from oilwatch.config import HomeConfig, Settings
from oilwatch.discovery import DiscoveryService
from oilwatch.models import SupplierCandidate


class FakeGeo:
    def __init__(self, coordinates: tuple[float, float] | None = (57.15, -2.09)) -> None:
        self.coordinates = coordinates
        self.queries: list[str] = []

    def geocode(self, query: str):
        self.queries.append(query)
        return (self.coordinates[0], self.coordinates[1], "Aberdeen") if self.coordinates else None

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
        home=HomeConfig(label="Hatton of Fintry, Aberdeenshire"),
    )
    base.update(overrides)
    return Settings(**base)


def _service(**overrides: Any) -> DiscoveryService:
    return DiscoveryService(_settings(**overrides), FakeGeo())


class UrlAndNameTests(unittest.TestCase):
    def test_unwraps_a_duckduckgo_redirect(self) -> None:
        url = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.co.uk%2Fprice"
        self.assertEqual(DiscoveryService._unwrap_duckduckgo_url(url), "https://example.co.uk/price")

    def test_plain_url_is_unchanged(self) -> None:
        self.assertEqual(
            DiscoveryService._unwrap_duckduckgo_url("https://example.co.uk/"), "https://example.co.uk/"
        )

    def test_redirect_without_a_target_is_empty(self) -> None:
        self.assertEqual(DiscoveryService._unwrap_duckduckgo_url("https://duckduckgo.com/l/?uddg="), "")

    def test_name_prefers_the_non_generic_part(self) -> None:
        self.assertEqual(
            DiscoveryService._supplier_name_from_title("Heating Oil Prices | Turriff Fuels", "x.co.uk"),
            "Turriff Fuels",
        )

    def test_home_prefix_takes_the_last_part(self) -> None:
        self.assertEqual(
            DiscoveryService._supplier_name_from_title("Home | Example Fuels", "example.co.uk"),
            "Example Fuels",
        )

    def test_name_falls_back_to_the_domain(self) -> None:
        self.assertEqual(
            DiscoveryService._supplier_name_from_title("No separator", "example-fuels.co.uk"),
            "Example Fuels",
        )


class FieldExtractionTests(unittest.TestCase):
    def test_email_from_a_mailto_link(self) -> None:
        self.assertEqual(
            DiscoveryService._extract_email('contact <a href="mailto:info@example.co.uk">us</a>'),
            "info@example.co.uk",
        )

    def test_no_email_is_none(self) -> None:
        self.assertIsNone(DiscoveryService._extract_email("no contact details"))

    def test_phone_number(self) -> None:
        self.assertEqual(DiscoveryService._extract_phone("Call us on 01224 877575 today"), "01224 877575")

    def test_address_includes_the_postcode(self) -> None:
        text = "Example Fuels, Inverurie, Aberdeenshire AB51 3AB, Scotland"
        address = DiscoveryService._extract_address(text)
        self.assertIsNotNone(address)
        self.assertIn("AB51 3AB", address)


class LocalHintTests(unittest.TestCase):
    def test_local_when_a_hint_appears(self) -> None:
        candidate = SupplierCandidate(name="X Fuels", website="https://x.co.uk", query="heating oil aberdeen")
        self.assertTrue(DiscoveryService._appears_local(candidate))

    def test_not_local_without_a_hint(self) -> None:
        candidate = SupplierCandidate(
            name="Yorkshire Fuels", website="https://x.co.uk", query="heating oil", title="Yorkshire Fuels"
        )
        self.assertFalse(DiscoveryService._appears_local(candidate))


class ExcludedDomainTests(unittest.TestCase):
    def test_matches_the_domain_and_its_subdomains(self) -> None:
        service = _service(excluded_domains=["boilerjuice.com"])
        self.assertTrue(service._is_excluded_domain("boilerjuice.com"))
        self.assertTrue(service._is_excluded_domain("www.boilerjuice.com"))
        self.assertFalse(service._is_excluded_domain("example.com"))


class HomeCoordinatesTests(unittest.TestCase):
    def test_uses_the_configured_coordinates(self) -> None:
        service = _service(home=HomeConfig(label="Home", latitude=57.1, longitude=-2.1))
        self.assertEqual(service.home_coordinates(), (57.1, -2.1))

    def test_geocodes_when_coordinates_are_missing(self) -> None:
        service = _service(home=HomeConfig(label="Hatton of Fintry"))
        self.assertEqual(service.home_coordinates(), (57.15, -2.09))

    def test_raises_when_the_home_cannot_be_geocoded(self) -> None:
        service = DiscoveryService(_settings(home=HomeConfig(label="Nowhere")), FakeGeo(coordinates=None))
        with self.assertRaises(RuntimeError):
            service.home_coordinates()


class ParseResultsTests(unittest.TestCase):
    HTML = """
    <div class="result">
      <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.turriff-fuels.co.uk%2F">Heating Oil Prices | Turriff Fuels</a>
      <a class="result__snippet">Cheap heating oil in Aberdeenshire</a>
    </div>
    <div class="result">
      <a class="result__a" href="https://example.com/blog/cats">Cute cats</a>
      <a class="result__snippet">nothing relevant here</a>
    </div>
    """

    def test_keeps_only_price_relevant_results_and_unwraps_urls(self) -> None:
        results = _service()._parse_results("heating oil aberdeen", self.HTML)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].website, "https://www.turriff-fuels.co.uk/")
        self.assertEqual(results[0].name, "Turriff Fuels")


if __name__ == "__main__":
    unittest.main()
