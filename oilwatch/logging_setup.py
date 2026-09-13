"""One place to configure OilWatch logging.

Library modules call :func:`get_logger` and log. The CLI, scheduler and MCP
entry points call :func:`configure_logging` once, so that those records actually
go somewhere — without it, library logging is discarded by Python's default
"handler of last resort" behaviour, which is how failures ended up invisible.

Level comes from ``OILWATCH_LOG_LEVEL`` (default ``INFO``): use ``DEBUG`` for
per-request detail, or ``WARNING`` to keep scheduled runs quiet.

A run that names a log file also writes to a rotating one. ``OILWATCH_LOG_FILE``
carries that path, and ``oilwatch_env.bat`` - which both unattended launchers
call - sets it. Those two are the scheduler started from the Startup folder and
the Task Scheduler's ``monitor_email.bat``, neither of which has a window to
print to, so a sweep that failed left nothing to look at. An interactive run
keeps its console and needs no file.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "oilwatch"
DEFAULT_LEVEL = "INFO"
LOG_FILE_ENV = "OILWATCH_LOG_FILE"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
#: The sweep runs hourly, so an unrotated log would grow without limit.
_MAX_BYTES = 1_000_000
_BACKUPS = 3


def configure_logging(level: str | None = None, log_path: Path | None = None) -> logging.Logger:
    """Configure and return the root ``oilwatch`` logger. Safe to call twice.

    Adds a rotating file handler when ``log_path`` is given or when
    ``OILWATCH_LOG_FILE`` names one. The console handler is kept either way, so
    asking for the file never silences a warning.
    """
    logger = logging.getLogger(LOGGER_NAME)
    resolved = (level or os.environ.get("OILWATCH_LOG_LEVEL") or DEFAULT_LEVEL).upper()
    logger.setLevel(getattr(logging, resolved, logging.INFO))
    if not logger.handlers:
        logger.addHandler(_stream_handler())
    log_file = log_path or os.environ.get(LOG_FILE_ENV)
    if log_file:
        _add_file_handler(logger, Path(log_file))
    return logger


def _stream_handler() -> logging.Handler:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FORMAT))
    return handler


def _add_file_handler(logger: logging.Logger, log_path: Path) -> None:
    """Attach one file handler per path, however often this is called.

    Idempotent by path rather than by "has a file handler", so a second
    configure_logging call cannot send every record to the file twice.
    """
    resolved = log_path.resolve()
    target = os.path.normcase(str(resolved))
    for handler in logger.handlers:
        existing = getattr(handler, "baseFilename", None)
        if existing and os.path.normcase(existing) == target:
            return
    resolved.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        resolved, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(handler)


def get_logger(suffix: str = "") -> logging.Logger:
    """Return a child logger, e.g. ``get_logger("connectors.valueoils")``."""
    if not suffix:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{suffix}")
