"""One place to configure OilWatch logging.

Library modules call :func:`get_logger` and log. The CLI, scheduler and MCP
entry points call :func:`configure_logging` once, so that those records actually
go somewhere — without it, library logging is discarded by Python's default
"handler of last resort" behaviour, which is how failures ended up invisible.

Level comes from ``OILWATCH_LOG_LEVEL`` (default ``INFO``): use ``DEBUG`` for
per-request detail, or ``WARNING`` to keep scheduled runs quiet.
"""

from __future__ import annotations

import logging
import os

LOGGER_NAME = "oilwatch"
DEFAULT_LEVEL = "INFO"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: str | None = None) -> logging.Logger:
    """Configure and return the root ``oilwatch`` logger. Safe to call twice."""
    logger = logging.getLogger(LOGGER_NAME)
    resolved = (level or os.environ.get("OILWATCH_LOG_LEVEL") or DEFAULT_LEVEL).upper()
    logger.setLevel(getattr(logging, resolved, logging.INFO))
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)
    return logger


def get_logger(suffix: str = "") -> logging.Logger:
    """Return a child logger, e.g. ``get_logger("connectors.valueoils")``."""
    if not suffix:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{suffix}")
