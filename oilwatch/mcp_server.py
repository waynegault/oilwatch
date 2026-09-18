"""MCP (Model Context Protocol) server exposing OilWatch to external agents.

Run locally::

    python -m oilwatch.mcp_server

This serves a streamable-HTTP MCP endpoint at ``http://127.0.0.1:8000/mcp`` by
default. To let an agent running in WSL reach this Windows host, bind to all
interfaces and connect from WSL using the Windows host's address::

    MCP_HOST=0.0.0.0 python -m oilwatch.mcp_server
    # from WSL: http://<windows-host-address>:8000/mcp

Host/port are configurable via ``MCP_HOST`` and ``MCP_PORT``. Passing ``--stdio``
serves the same tools over stdio instead, for a client that launches the server
on demand rather than requiring a listening service::

    python -m oilwatch.mcp_server --stdio
"""

from __future__ import annotations

import os
import sys
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

mcp = FastMCP(
    "oilwatch",
    version=__version__,
    instructions=(
        "OilWatch tracks domestic heating-oil prices for the owner's configured "
        "delivery postcode. Prices are GBP per litre inclusive of 5% VAT."
    ),
)

#: Built on first use. Importing this module must not read config or open a
#: database, so a tool registry (or a test) can import it freely.
_app: OilWatchApp | None = None


def _get_app() -> OilWatchApp:
    global _app
    if _app is None:
        _app = OilWatchApp(ROOT)
    return _app

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
    return _get_app().suppliers(include_inactive=False)


@mcp.tool(title="Current prices", annotations=_READ_ONLY)
def current_prices() -> dict[str, Any]:
    """Return the latest recorded price for each supplier (£/L inc. VAT).

    An envelope rather than a bare list, so an empty result explains itself:
    ``quotes`` holds the suppliers with a current price, and its companions say
    which reason applies when that is empty or thin — ``window_days`` (the recency
    window applied), ``as_of`` (the freshest observation in ``quotes``, or None
    when there is none), ``excluded_suppliers`` (quotes older than the window),
    ``never_quoted`` (no price has ever been recorded from them),
    ``not_refreshed_suppliers`` (priced earlier, latest attempt failed),
    ``no_quote_suppliers`` (the last ask returned no price — often because there
    is no web quote to read), and ``failed_suppliers`` (the last ask raised).

    Each row carries ``valid_until``, its ``kind`` (``supplier`` or
    ``benchmark``), its ``order_channel`` — ``web``, ``phone_email``, ``phone``,
    ``email``, ``benchmark``, or ``none`` when nothing is recorded — a
    ``contact`` of ``{phone, email, url}``, and the supplier's ``order_page``
    when its config records one. ``contact.url`` is the single link to act on:
    the ordering page when there is one, otherwise the site, which may only be a
    marketing page. ``order_page`` being ``None`` means unrecorded rather than
    "there is no page".

    The suppliers that are *not* in ``quotes`` — ``no_quote_suppliers``,
    ``failed_suppliers`` and ``never_quoted`` — carry the same ``kind``,
    ``order_channel``, ``order_page`` and ``contact``, because those are the rows
    a reader has to *ask*, and a reason without a way to act on it is half an
    answer.
    """
    return _get_app().current_prices()


@mcp.tool(title="Cheapest supplier", annotations=_READ_ONLY)
def cheapest() -> dict[str, Any]:
    """Return the cheapest supplier plus market average and variance.

    ``excluded_suppliers`` lists any supplier whose newest quote fell outside
    ``max_quote_age_days``, so a thin market is visible as "not re-quoted yet".
    ``window_days`` states the window that comparison was made against, so an
    empty market is not read as a broken one. The winner carries its ``kind``,
    ``order_channel``, ``contact`` and ``order_page``, so a reader never has to
    work out how to act on the price; a ``kind`` of ``benchmark`` is not a
    supplier you can buy from, and ``website`` stays the general link, which may
    be a marketing page.
    """
    return _get_app().cheapest()


@mcp.tool(title="Recorded purchases", annotations=_READ_ONLY)
def purchases() -> list[dict[str, Any]]:
    """Return purchases already recorded, newest first, with totals and codes.

    Recording happens through the CLI (``oilwatch record-purchase``), which is a
    deliberate act by the owner; this tool only reads them back.
    """
    return _get_app().purchases()


@mcp.tool(title="Market status", annotations=_READ_ONLY)
def status() -> dict[str, Any]:
    """Return the market snapshot, price trend, and a buy/hold recommendation.

    Also names the suppliers the most recent ask could not price, split into
    ``no_quote_suppliers`` (they gave no price) and ``failed_suppliers`` (the
    retrieval failed), so a report can give both without reading every note.
    """
    return _get_app().status()


@mcp.tool(title="Build market chart", annotations=_LOCAL_ARTIFACT)
def chart() -> str:
    """Generate the market summary chart and return its file path."""
    return _get_app().chart()


@mcp.tool(title="Build time-series chart", annotations=_LOCAL_ARTIFACT)
def time_series_chart() -> str:
    """Generate the per-supplier + Brent crude time-series chart and return its path."""
    return _get_app().time_series_chart()


#: How long a completed sweep is honoured before another one is started.
#: A sweep is minutes of real browsers with CAPTCHA risk, and the likeliest way
#: to start a second is a client whose per-call timeout expired mid-scrape and
#: retried — mcporter's default is 60 s against a 1-3 minute sweep. Ten minutes
#: is longer than any sweep and far shorter than a quote's one-day life, so it
#: never costs a fresh answer.
REFRESH_COOLDOWN_MINUTES = 10


@mcp.tool(title="Refresh prices (slow)", annotations=_EXTERNAL_SLOW)
def refresh_prices(postcode: str | None = None, force: bool = False) -> dict[str, Any]:
    """Scrape fresh quotes from all suppliers (slow: uses browser automation).

    Takes minutes and may fail per-supplier (CAPTCHA, blocked, site down);
    partial results are normal. Do not call it in a loop. Each row carries a
    ``status`` and, when it is not ``ok``, a machine-readable ``reason`` —
    ``no_quote_page`` (no web quote exists; use the contact details),
    ``quote_by_request`` (a quote page exists but answers a person, so the
    supplier must be asked rather than scraped),
    ``login_not_confirmed`` (an authenticated portal did not sign in),
    ``captcha``, or ``site_error`` — so a failure is reportable without reading
    ``notes``. ``None`` means unclassified, not "no reason".

    Returns an envelope rather than a bare list, because the likeliest way to
    reach it twice is a client timeout mid-sweep followed by a retry. When a
    sweep ran within ``cooldown_minutes``, nothing is scraped and ``cached`` is
    true: ``results`` is then empty, and ``refreshed_at`` with ``minutes_ago``
    date the prices already on record — read ``current_prices`` instead of
    retrying. ``force=True`` sweeps regardless.
    """
    app = _get_app()
    if not force:
        recent = app.refresh_recently_done(REFRESH_COOLDOWN_MINUTES)
        if recent is not None:
            return {
                "cached": True,
                "cooldown_minutes": REFRESH_COOLDOWN_MINUTES,
                "refreshed_at": recent["refreshed_at"],
                "minutes_ago": recent["minutes_ago"],
                "results": [],
                "note": (
                    "No sweep was started: one ran "
                    f"{recent['minutes_ago']} minutes ago. Read current_prices for "
                    "the prices on record, or pass force=true for a fresh sweep."
                ),
            }

    results = app.quote_all(postcode=postcode or load_contact().postcode, prefer_browser=True)
    observed = [row["observed_at"] for row in results if row.get("observed_at")]
    return {
        "cached": False,
        "cooldown_minutes": REFRESH_COOLDOWN_MINUTES,
        "refreshed_at": max(observed) if observed else None,
        "results": results,
    }


@mcp.tool(title="Update Brent crude", annotations=_EXTERNAL_FETCH)
def update_brent() -> dict[str, Any]:
    """Fetch the latest Brent crude daily series from the EIA."""
    return _get_app().update_brent()


def main() -> None:
    """Run the server. Console-script entry point.

    Defaults to the streamable-HTTP endpoint. ``--stdio`` serves the same tools
    over stdio instead, so an MCP client can spawn the server on demand and no
    listening socket (or host address) is needed.
    """
    configure_logging()
    if "--stdio" in sys.argv[1:]:
        mcp.run(transport="stdio")
        return
    mcp.run(transport="http", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
