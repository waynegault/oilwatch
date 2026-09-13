from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from oilwatch.http import build_client, request_with_retry
from oilwatch.logging_setup import LOG_FILE_ENV, LOGGER_NAME, configure_logging


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

    def _close_file_handlers(self) -> None:
        """Release the log files a test opened, so its temp dir can be removed."""
        for handler in list(self.logger.handlers):
            if handler not in self._handlers:
                handler.close()

    def test_configure_logging_is_idempotent(self) -> None:
        first = configure_logging("DEBUG")
        second = configure_logging("DEBUG")
        self.assertIs(first, second)
        self.assertEqual(len(first.handlers), 1, "handlers must not stack up")
        self.assertEqual(first.level, logging.DEBUG)
        self.assertEqual(first.name, LOGGER_NAME)

    def test_unrecognised_level_falls_back_to_info(self) -> None:
        self.assertEqual(configure_logging("NOT-A-LEVEL").level, logging.INFO)

    def test_a_log_path_is_created_and_written_to(self) -> None:
        """The scheduler and the scheduled sweep run with no console to read."""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "nested" / "oilwatch.log"
            try:
                logger = configure_logging("INFO", log_path)
                logger.warning("sweep failed: site down")
                written = log_path.read_text(encoding="utf-8")
            finally:
                self._close_file_handlers()

        self.assertIn("sweep failed: site down", written)

    def test_the_file_handler_does_not_stack_up(self) -> None:
        """Configured twice for one path, records must not land in it twice."""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "oilwatch.log"
            try:
                first = configure_logging("INFO", log_path)
                before = len(first.handlers)
                second = configure_logging("INFO", log_path)
                self.assertEqual(len(second.handlers), before, "records must not double")
            finally:
                self._close_file_handlers()

    def test_asking_for_the_log_file_keeps_the_console_handler(self) -> None:
        """The file is in addition to the console, never instead of it."""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "oilwatch.log"
            try:
                logger = configure_logging("INFO", log_path)
                console = [
                    handler
                    for handler in logger.handlers
                    if type(handler) is logging.StreamHandler
                ]
                self.assertEqual(len(console), 1, "the console handler must survive")
            finally:
                self._close_file_handlers()

    def test_the_launch_env_var_names_the_log_file(self) -> None:
        """This variable is how the unattended launchers ask for the log.

        The scheduler and the scheduled sweep are started by scripts, not by a
        person, so if this name drifts the file silently stops being written.
        """
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "oilwatch.log"
            try:
                with patch.dict(os.environ, {LOG_FILE_ENV: str(log_path)}):
                    logger = configure_logging("INFO")
                    logger.warning("swept the inbox")
                written = log_path.read_text(encoding="utf-8")
            finally:
                self._close_file_handlers()

        self.assertIn("swept the inbox", written)


if __name__ == "__main__":
    unittest.main()
