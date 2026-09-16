from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, cast

from geopy.distance import geodesic
from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from geopy.geocoders import Nominatim


class LocationLike(Protocol):
    """The fields of a geopy ``Location`` this package reads."""

    latitude: float
    longitude: float
    address: str


class GeocoderLike(Protocol):
    """The geocoder surface ``GeoService`` drives, declared rather than imported.

    geopy *generates* ``geocode`` behind a decorator that switches on the adapter
    — ``geopy/geocoders/base.py`` has the synchronous ``def geocode`` commented
    out and a decorator testing ``BaseAsyncAdapter`` — so a synchronous call to
    ``Nominatim.geocode`` reads as a coroutine, and its result as an unknown.
    Saying here what we actually ask for — one location, or none — is what makes
    the fields read below checked instead of guessed at.
    """

    def geocode(self, query: str, timeout: float | None = None) -> LocationLike | None: ...


class GeoServiceLike(Protocol):
    """The ``GeoService`` surface the rest of the package uses.

    Deliberately not a ``GeocoderLike``: that is the inner collaboration with
    geopy, where a result is a ``Location``. This facade hands back plain
    ``(latitude, longitude, address)`` tuples.
    """

    def geocode(self, query: str) -> tuple[float, float, str] | None: ...

    def geocode_first(self, queries: Iterable[str]) -> tuple[float, float, str] | None: ...

    def distance_miles(self, a: tuple[float, float], b: tuple[float, float]) -> float: ...


class GeoService:
    def __init__(self, user_agent: str = "oilwatch") -> None:
        # One cast, at the boundary, rather than per call: geopy's generated
        # ``geocode`` cannot satisfy the protocol above, and the way we call it —
        # no arguments beyond the query and the timeout, so ``exactly_one`` keeps
        # its default — does return a single Location.
        self._geocoder: GeocoderLike = cast(GeocoderLike, Nominatim(user_agent=user_agent))

    def geocode(self, query: str) -> tuple[float, float, str] | None:
        try:
            location = self._geocoder.geocode(query, timeout=10)
        except (GeocoderTimedOut, GeocoderServiceError):
            return None
        if not location:
            return None
        return (float(location.latitude), float(location.longitude), location.address)

    def geocode_first(self, queries: Iterable[str]) -> tuple[float, float, str] | None:
        for query in queries:
            if not query:
                continue
            location = self.geocode(query)
            if location:
                return location
        return None

    @staticmethod
    def distance_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
        return float(geodesic(a, b).miles)
