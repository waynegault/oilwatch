"""Shared HTTP client setup and retry for the supplier connectors.

Retry is deliberately narrow. Transient faults — connection resets, read
timeouts, and retryable HTTP statuses — are retried with exponential backoff and
jitter. **4xx is never retried**: a 403 or a 404 will not fix itself, and
retrying only delays the connector's fallback to a manual quote.

Two layers, because they cover different failures:

* ``build_client`` sets ``httpx.HTTPTransport(retries=...)``, which re-attempts
  connection-level errors inside httpx.
* :func:`request_with_retry` adds status-code retries and backoff, for callers
  that want it.
"""

from __future__ import annotations

import random
import time

import httpx

from oilwatch.logging_setup import get_logger

log = get_logger("http")

DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 0.5
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    )
}

#: Statuses worth a second attempt. 429 is included because suppliers rate-limit.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def build_client(
    *,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    follow_redirects: bool = True,
    headers: dict[str, str] | None = None,
) -> httpx.Client:
    """Return an ``httpx.Client`` with timeouts, headers and transport retries."""
    return httpx.Client(
        timeout=timeout,
        follow_redirects=follow_redirects,
        headers={**DEFAULT_HEADERS, **(headers or {})},
        transport=httpx.HTTPTransport(retries=retries),
    )


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    attempts: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_BACKOFF,
    sleep=time.sleep,
    **kwargs,
) -> httpx.Response:
    """Perform a request, retrying transient failures with jittered backoff.

    ``sleep`` is injectable so tests do not actually wait.
    """
    attempts = max(1, attempts)
    last_error: Exception | None = None
    response: httpx.Response | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt == attempts:
                break
            delay = _delay(backoff, attempt)
            log.warning(
                "%s %s failed (%s); attempt %d/%d, retrying in %.2fs",
                method, url, exc, attempt, attempts, delay,
            )
            sleep(delay)
            continue

        if response.status_code in RETRY_STATUSES and attempt < attempts:
            delay = _delay(backoff, attempt)
            log.warning(
                "%s %s returned HTTP %d; attempt %d/%d, retrying in %.2fs",
                method, url, response.status_code, attempt, attempts, delay,
            )
            sleep(delay)
            continue

        return response

    if last_error is not None:
        raise last_error
    assert response is not None  # only reachable if attempts >= 1 succeeded
    return response


def _delay(backoff: float, attempt: int) -> float:
    """Exponential backoff with jitter, so parallel retries do not sync up."""
    return backoff * (2 ** (attempt - 1)) + random.uniform(0, backoff)
