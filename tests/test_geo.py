"""GeoService: geocoding and distance, with the network replaced."""

from __future__ import annotations

import types
import unittest

from geopy.exc import GeocoderTimedOut

from oilwatch.geo import GeoService


def _location(lat: float, lon: float, address: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(latitude=lat, longitude=lon, address=address)


class ScriptedGeocoder:
    """Returns a result (or raises) per query, recording what was asked."""

    def __init__(self, results: dict) -> None:
        self._results = results
        self.queries: list[str] = []

    def geocode(self, query: str, timeout: int | None = None):
        self.queries.append(query)
        result = self._results.get(query)
        if isinstance(result, Exception):
            raise result
        return result


class GeocodeTests(unittest.TestCase):
    def test_returns_coordinates_and_address(self) -> None:
        service = GeoService()
        service._geocoder = ScriptedGeocoder({"AB21 0YA": _location(57.15, -2.09, "Aberdeen")})
        self.assertEqual(service.geocode("AB21 0YA"), (57.15, -2.09, "Aberdeen"))

    def test_unknown_place_is_none(self) -> None:
        service = GeoService()
        service._geocoder = ScriptedGeocoder({"nowhere": None})
        self.assertIsNone(service.geocode("nowhere"))

    def test_service_error_is_none_not_raised(self) -> None:
        service = GeoService()
        service._geocoder = ScriptedGeocoder({"x": GeocoderTimedOut("timeout")})
        self.assertIsNone(service.geocode("x"))


class GeocodeFirstTests(unittest.TestCase):
    def test_skips_blanks_and_misses_and_returns_the_first_hit(self) -> None:
        service = GeoService()
        geocoder = ScriptedGeocoder(
            {"postcode": None, "address": _location(57.0, -2.0, "Aberdeen")}
        )
        service._geocoder = geocoder

        result = service.geocode_first(["", "postcode", "address", "ignored"])

        self.assertEqual(result, (57.0, -2.0, "Aberdeen"))
        self.assertEqual(geocoder.queries, ["postcode", "address"])

    def test_all_misses_is_none(self) -> None:
        service = GeoService()
        service._geocoder = ScriptedGeocoder({})
        self.assertIsNone(service.geocode_first(["a", "b"]))


class DistanceTests(unittest.TestCase):
    def test_same_point_is_zero(self) -> None:
        self.assertEqual(GeoService.distance_miles((57.15, -2.09), (57.15, -2.09)), 0.0)

    def test_one_degree_of_longitude_at_the_equator(self) -> None:
        # 1 degree of longitude at the equator is ~69.17 statute miles.
        self.assertAlmostEqual(
            GeoService.distance_miles((0.0, 0.0), (0.0, 1.0)), 69.17, delta=0.1
        )


if __name__ == "__main__":
    unittest.main()
