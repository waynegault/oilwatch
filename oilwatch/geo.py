from __future__ import annotations

from typing import Iterable

from geopy.distance import geodesic
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderServiceError, GeocoderTimedOut


class GeoService:
    def __init__(self, user_agent: str = "oilwatch") -> None:
        self._geocoder = Nominatim(user_agent=user_agent)

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
