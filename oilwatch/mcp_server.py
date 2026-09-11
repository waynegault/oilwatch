"""MCP (Model Context Protocol) server exposing OilWatch to external agents.

Run locally::

    python -m oilwatch.mcp_server

This serves a streamable-HTTP MCP endpoint at ``http://127.0.0.1:8000/mcp`` by
default. To let an agent running in WSL reach this Windows host, bind to all
interfaces and connect from WSL using the Windows host's address::

    MCP_HOST=0.0.0.0 python -m oilwatch.mcp_server
    # from WSL: http://<windows-host-address>:8000/mcp

Host/port are configurable via ``MCP_HOST`` and ``MCP_PORT``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from oilwatch import __version__
from oilwatch.identity import load_contact
from oilwatch.logging_setup import configure_logging
from oilwatch.service import OilWatchApp

# Resolve the repo root from this file's location so the server works no matter
# which working directory it is launched from.
ROOT = Path(__file__).resolve().parents[1]
# .strip() guards against cmd.exe's `set VAR=x && ...` trailing-space quirk.
HOST = os.environ.get("MCP_HOST", "127.0.0.1").strip()
PORT = int(os.environ.get("MCP_PORT", "8000").strip())

_app = OilWatchApp(ROOT)
_contact = load_contact()

mcp = FastMCP(
    "oilwatch",
    version=__version__,
    instructions=(
        f"OilWatch tracks domestic heating-oil prices around {_app.settings.home.label} "
        f"({_contact.postcode}). Prices are GBP per litre inclusive of 5% VAT."
    ),
)

# Tool annotations. MCP defaults are the pessimistic ones (destructiveHint=True,
# openWorldHint=True), so a client that gates on annotations - OpenClaw prompts
# for approval on every unannotated call - needs them stated explicitly.
_READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
_LOCAL_ARTIFACT = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
_EXTERNAL_FETCH = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
_EXTERNAL_SLOW = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
)


@mcp.tool(title="List suppliers", annotations=_READ_ONLY)
def list_suppliers() -> list[dict[str, Any]]:
    """List all known heating-oil suppliers."""
    return _app.suppliers(include_inactive=False)


@mcp.tool(title="Current prices", annotations=_READ_ONLY)
def current_prices() -> list[dict[str, Any]]:
    """Return the latest recorded price for each supplier (£/L inc. VAT).

    Suppliers with no quote inside the configured ``max_quote_age_days`` window
    are omitted, so historical spreadsheet rows cannot masquerade as today's
    prices.
    """
    return _app.current_prices()


@mcp.tool(title="Cheapest supplier", annotations=_READ_ONLY)
def cheapest() -> dict[str, Any]:
    """Return the cheapest supplier plus market average and variance."""
    return _app.cheapest()


@mcp.tool(title="Recorded purchases", annotations=_READ_ONLY)
def purchases() -> list[dict[str, Any]]:
    """Return purchases already recorded, newest first, with totals and codes.

    Recording happens through the CLI (``oilwatch record-purchase``), which is a
    deliberate act by the owner; this tool only reads them back.
    """
    return _app.purchases()


@mcp.tool(title="Market status", annotations=_READ_ONLY)
def status() -> dict[str, Any]:
    """Return the market snapshot, price trend, and a buy/hold recommendation."""
    return _app.status()


@mcp.tool(title="Build market chart", annotations=_LOCAL_ARTIFACT)
def chart() -> str:
    """Generate the market summary chart and return its file path."""
    return _app.chart()


@mcp.tool(title="Build time-series chart", annotations=_LOCAL_ARTIFACT)
def time_series_chart() -> str:
    """Generate the per-supplier + Brent crude time-series chart and return its path."""
    return _app.time_series_chart()


@mcp.tool(title="Refresh prices (slow)", annotations=_EXTERNAL_SLOW)
def refresh_prices(postcode: str | None = None) -> list[dict[str, Any]]:
    """Scrape fresh quotes from all suppliers (slow: uses browser automation).

    Takes minutes and may fail per-supplier (CAPTCHA, blocked, site down);
    partial results are normal. Do not call it in a loop.
    """
    return _app.quote_all(postcode=postcode or load_contact().postcode, prefer_browser=True)


@mcp.tool(title="Update Brent crude", annotations=_EXTERNAL_FETCH)
def update_brent() -> dict[str, Any]:
    """Fetch the latest Brent crude daily series from the EIA."""
    return _app.update_brent()


def main() -> None:
    """Run the streamable-HTTP endpoint. Console-script entry point."""
    configure_logging()
    mcp.run(transport="http", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
