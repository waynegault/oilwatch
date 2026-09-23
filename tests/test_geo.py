"""GeoService: geocoding and distance, with the network replaced."""

from __future__ import annotations

import types
import unittest

from geopy.exc import GeocoderRateLimited, GeocoderTimedOut

from oilwatch.geo import GeoService


def _location(lat: float, lon: float, address: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(latitude=lat, longitude=lon, address=address)


class ScriptedGeocoder:
    """Returns a result (or raises) per query, recording what was asked."""

    def __init__(self, results: dict) -> None:
        self._results = results
        self.queries: list[str] = []

    def geocode(self, query: str, timeout: float | None = None):
        self.queries.append(query)
        result = self._results.get(query)
        if isinstance(result, Exception):
            raise result
        return result


class ThrottledGeocoder:
    """Raises the throttles it is given, then answers, counting the attempts."""

    def __init__(self, throttles: int, result=None) -> None:
        self._remaining = throttles
        self._result = result
        self.calls = 0

    def geocode(self, query: str, timeout: float | None = None):
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise GeocoderRateLimited("429", retry_after=2.5)
        return self._result


class FakeClock:
    """A monotonic clock that advances by whatever the service sleeps."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


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
        clock = FakeClock()
        service = GeoService(clock=clock, sleep=clock.sleep)
        geocoder = ScriptedGeocoder(
            {"postcode": None, "address": _location(57.0, -2.0, "Aberdeen")}
        )
        service._geocoder = geocoder

        result = service.geocode_first(["", "postcode", "address", "ignored"])

        self.assertEqual(result, (57.0, -2.0, "Aberdeen"))
        self.assertEqual(geocoder.queries, ["postcode", "address"])

    def test_all_misses_is_none(self) -> None:
        clock = FakeClock()
        service = GeoService(clock=clock, sleep=clock.sleep)
        service._geocoder = ScriptedGeocoder({})
        self.assertIsNone(service.geocode_first(["a", "b"]))


class RateLimitTests(unittest.TestCase):
    """A throttle is not a place that does not exist.

    Nominatim's limit is one request a second and ``GeocoderRateLimited`` subclasses
    ``GeocoderServiceError``, so a 429 used to take the same path as "no such
    address": discovery recorded "Could not geocode supplier." for every candidate
    in a burst and nothing anywhere said the geocoder had stopped answering.
    """

    def test_requests_are_spaced_to_the_policy(self) -> None:
        """A run asks for several spellings of one place, so it spaces them itself."""
        clock = FakeClock()
        service = GeoService(clock=clock, sleep=clock.sleep)
        service._geocoder = ScriptedGeocoder(
            {"a": _location(1.0, 2.0, "A"), "b": _location(3.0, 4.0, "B")}
        )

        service.geocode("a")
        clock.now += 0.2  # the network took 200 ms on its own
        service.geocode("b")

        self.assertEqual(len(clock.slept), 1, "only the second ask has an interval to wait out")
        self.assertAlmostEqual(clock.slept[0], 0.8, places=6)

    def test_a_throttle_is_waited_out_on_the_providers_own_remedy(self) -> None:
        clock = FakeClock()
        service = GeoService(clock=clock, sleep=clock.sleep)
        geocoder = ThrottledGeocoder(throttles=1, result=_location(57.0, -2.0, "Aberdeen"))
        service._geocoder = geocoder

        self.assertEqual(service.geocode("address"), (57.0, -2.0, "Aberdeen"))
        self.assertEqual(geocoder.calls, 2, "the ask was repeated after the throttle")
        self.assertEqual(clock.slept, [2.5], "geopy reported Retry-After: 2.5")

    def test_a_throttle_that_outlasts_the_attempts_says_so(self) -> None:
        clock = FakeClock()
        service = GeoService(clock=clock, sleep=clock.sleep)
        geocoder = ThrottledGeocoder(throttles=99)
        service._geocoder = geocoder

        with self.assertLogs("oilwatch.geo", level="WARNING") as captured:
            self.assertIsNone(service.geocode("somewhere"))

        text = "\n".join(captured.output)
        self.assertIn("rate-limited", text, "the throttle is named as a throttle")
        self.assertIn("'somewhere'", text, "and the query it refused is named with it")
        self.assertIn("rather than treating it as ungeocodable", text, "not a verdict on the place")
        self.assertEqual(geocoder.calls, GeoService.RATE_LIMIT_ATTEMPTS)


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
