from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Protocol, cast

from geopy.distance import geodesic
from geopy.exc import GeocoderRateLimited, GeocoderServiceError, GeocoderTimedOut
from geopy.geocoders import Nominatim

from oilwatch.logging_setup import get_logger

log = get_logger("geo")


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
    """Nominatim, paced to its usage policy and honest about being throttled.

    Nominatim asks for at most one request a second, and a discovery run asks for
    several spellings of every candidate one after another - so it draws the limit
    and the provider answers 429. That 429 arrives as ``GeocoderRateLimited``, a
    subclass of the ``GeocoderServiceError`` this class already treated as "no such
    place": a throttled run was indistinguishable from one where the address
    genuinely does not resolve, and discovery logged "Could not geocode supplier."
    for every candidate either way - a fact about Nominatim's patience, reported as
    a fact about the supplier.

    So requests are spaced to the policy rather than merely retried (a retry
    against a per-second limit fails again), a throttle is waited out on the
    provider's own ``Retry-After`` when it sends one, and a throttle that outlasts
    the attempts is logged as a throttle.
    """

    #: Nominatim's usage policy, not a tuned preference.
    MIN_INTERVAL_SECONDS = 1.0
    #: One retry after the first throttle, then give up and say so.
    RATE_LIMIT_ATTEMPTS = 2

    def __init__(
        self,
        user_agent: str = "oilwatch",
        *,
        clock=time.monotonic,
        sleep=time.sleep,
    ) -> None:
        # One cast, at the boundary, rather than per call: geopy's generated
        # ``geocode`` cannot satisfy the protocol above, and the way we call it —
        # no arguments beyond the query and the timeout, so ``exactly_one`` keeps
        # its default — does return a single Location.
        self._geocoder: GeocoderLike = cast(GeocoderLike, Nominatim(user_agent=user_agent))
        self._clock = clock
        self._sleep = sleep
        self._last_call: float | None = None

    def geocode(self, query: str) -> tuple[float, float, str] | None:
        for attempt in range(self.RATE_LIMIT_ATTEMPTS):
            self._pace()
            try:
                location = self._geocoder.geocode(query, timeout=10)
            except GeocoderRateLimited as exc:
                if attempt + 1 < self.RATE_LIMIT_ATTEMPTS:
                    self._sleep(_retry_after(exc, self.MIN_INTERVAL_SECONDS))
                    continue
                log.warning(
                    "nominatim rate-limited %r on %d attempt(s); leaving it ungeocoded "
                    "rather than treating it as ungeocodable - a later run can ask again",
                    query,
                    self.RATE_LIMIT_ATTEMPTS,
                )
                return None
            except (GeocoderTimedOut, GeocoderServiceError):
                return None
            if not location:
                return None
            return (float(location.latitude), float(location.longitude), location.address)
        return None

    def _pace(self) -> None:
        """Hold off until the provider's interval has passed since the last ask.

        Measured from the start of the previous request, so a request that slept
        its way through a throttle is not asked for a second one on top.
        """
        now = self._clock()
        if self._last_call is not None:
            remaining = self.MIN_INTERVAL_SECONDS - (now - self._last_call)
            if remaining > 0:
                self._sleep(remaining)
        self._last_call = now

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


def _retry_after(exc: GeocoderRateLimited, default: float) -> float:
    """The provider's own ``Retry-After`` in seconds, or ``default``.

    Typed by hand because the attribute is whatever the adapter put there: geopy
    passes the parsed header through, and a value that is not a number is no
    reason to wait a NaN, nor to raise out of a call that only wanted a location.
    """
    wait = exc.retry_after
    return float(wait) if isinstance(wait, (int, float)) else default
