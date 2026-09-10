from __future__ import annotations

import logging
import unittest

import httpx

from oilwatch.http import build_client, request_with_retry
from oilwatch.logging_setup import LOGGER_NAME, configure_logging


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _no_sleep(_seconds: float) -> None:
    """Retry tests must not actually wait."""


class RetryTests(unittest.TestCase):
    """assertLogs both captures the retry warning and keeps it off stderr."""

    def test_retries_a_retryable_status_then_succeeds(self) -> None:
        calls = []

        def handler(request):
            calls.append(request.url)
            if len(calls) < 3:
                return httpx.Response(503)
            return httpx.Response(200, text="ok")

        with self.assertLogs("oilwatch.http", level="WARNING"):
            response = request_with_retry(
                _client(handler), "GET", "https://example.com/quote", sleep=_no_sleep
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 3)

    def test_never_retries_a_404(self) -> None:
        calls = []

        def handler(request):
            calls.append(request.url)
            return httpx.Response(404)

        response = request_with_retry(
            _client(handler), "GET", "https://example.com/missing", sleep=_no_sleep
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(calls), 1, "4xx must not be retried")

    def test_retries_a_connection_error_then_succeeds(self) -> None:
        calls = []

        def handler(request):
            calls.append(request.url)
            if len(calls) == 1:
                raise httpx.ConnectError("connection reset", request=request)
            return httpx.Response(200, text="ok")

        with self.assertLogs("oilwatch.http", level="WARNING"):
            response = request_with_retry(
                _client(handler), "GET", "https://example.com/quote", sleep=_no_sleep
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 2)

    def test_reraises_when_every_attempt_fails(self) -> None:
        def handler(request):
            raise httpx.ConnectError("always down", request=request)

        with self.assertLogs("oilwatch.http", level="WARNING"), self.assertRaises(httpx.ConnectError):
            request_with_retry(
                _client(handler),
                "GET",
                "https://example.com/quote",
                attempts=2,
                sleep=_no_sleep,
            )

    def test_gives_up_after_attempts_on_a_retryable_status(self) -> None:
        calls = []

        def handler(request):
            calls.append(request.url)
            return httpx.Response(503)

        with self.assertLogs("oilwatch.http", level="WARNING"):
            response = request_with_retry(
                _client(handler),
                "GET",
                "https://example.com/quote",
                attempts=2,
                sleep=_no_sleep,
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(calls), 2)


class BuildClientTests(unittest.TestCase):
    def test_sets_a_browser_user_agent_and_timeout(self) -> None:
        client = build_client()
        try:
            self.assertIn("Mozilla/5.0", client.headers["User-Agent"])
            self.assertEqual(client.timeout.read, 30.0)
        finally:
            client.close()

    def test_extra_headers_override_the_defaults(self) -> None:
        client = build_client(headers={"Accept": "text/html"})
        try:
            self.assertEqual(client.headers["Accept"], "text/html")
        finally:
            client.close()


class LoggingSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        # configure_logging attaches a real handler; without restoring it the
        # retry warnings from other tests would be printed into the test output.
        self.logger = logging.getLogger(LOGGER_NAME)
        self._handlers = list(self.logger.handlers)
        self._level = self.logger.level

    def tearDown(self) -> None:
        self.logger.handlers = self._handlers
        self.logger.setLevel(self._level)

    def test_configure_logging_is_idempotent(self) -> None:
        first = configure_logging("DEBUG")
        second = configure_logging("DEBUG")
        self.assertIs(first, second)
        self.assertEqual(len(first.handlers), 1, "handlers must not stack up")
        self.assertEqual(first.level, logging.DEBUG)
        self.assertEqual(first.name, LOGGER_NAME)

    def test_unrecognised_level_falls_back_to_info(self) -> None:
        self.assertEqual(configure_logging("NOT-A-LEVEL").level, logging.INFO)


if __name__ == "__main__":
    unittest.main()
