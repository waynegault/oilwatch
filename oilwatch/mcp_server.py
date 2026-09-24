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
    ``benchmark``), its ``order_channel`` — ``web``, ``email``, ``benchmark``,
    or ``none`` when no page and no address are recorded — a
    ``contact`` of ``{phone, email, url}``, and the supplier's ``order_page``
    when its config records one. ``contact.url`` is the single link to act on:
    the ordering page when there is one, otherwise the site, which may only be a
    marketing page. ``order_page`` being ``None`` means unrecorded rather than
    "there is no page". A phone number alone is not an ``order_channel``: the
    app never rings a supplier, so ``contact.phone`` is data to pass on, not a
    route it takes.

    The suppliers that are *not* in ``quotes`` — ``no_quote_suppliers``,
    ``failed_suppliers`` and ``never_quoted`` — carry the same ``kind``,
    ``order_channel``, ``order_page`` and ``contact``, because those are the rows
    a reader has to *ask*, and a reason without a way to act on it is half an
    answer.

    ``refresh`` says whether a sweep is running right now: ``in_progress`` with
    ``started_by`` and ``seconds_ago``, or ``stale`` when one started and never
    reported back. That is how a caller whose own ``refresh_prices`` timed out
    learns whether to wait or to give up, without starting a second sweep to find
    out.
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

    ``refresh`` is the last sweep's marker, not a quote timestamp: its
    ``started_at`` and ``finished_at`` date the run itself (``started_by`` names
    what kicked it off), so ``seconds_ago`` ages the sweep and says nothing about
    how fresh the prices it left are — those carry their own ``observed_at``.
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
def refresh_prices(
    postcode: str | None = None, force: bool = False, background: bool = False
) -> dict[str, Any]:
    """Scrape fresh quotes from all suppliers (slow: uses browser automation).

    Takes minutes and may fail per-supplier (CAPTCHA, blocked, site down);
    partial results are normal. Do not call it in a loop. Each row carries a
    ``status`` and, when it is not ``ok``, a machine-readable ``reason`` —
    ``no_quote_page`` (no web quote exists; use the contact details),
    ``quote_by_request`` (a quote page exists but answers a person, so the
    supplier must be asked rather than scraped),
    ``browser_required`` (this path cannot price the supplier and browser
    automation can),
    ``login_not_confirmed`` (an authenticated portal did not sign in),
    ``captcha``, ``no_price_found`` (the page answered and carried no price), or
    ``site_error`` — so a failure is reportable without reading ``notes``.
    ``None`` means unclassified, not "no reason".

    ``background=True`` starts the sweep as its own detached process and returns
    ``{job_id, state, started_at, total}`` at once, for a client whose per-call
    timeout is shorter than a sweep: read ``refresh_status(job_id)`` for progress
    and results. Prefer it over a foreground call you expect to time out. The
    worker outlives this session, which is the point — nothing here waits.

    Returns an envelope rather than a bare list, because the likeliest way to
    reach it twice is a client timeout mid-sweep followed by a retry. When a
    sweep ran within ``cooldown_minutes``, nothing is scraped and ``cached`` is
    true: ``results`` is then empty, and ``refreshed_at`` with ``minutes_ago``
    date the prices already on record — read ``current_prices`` instead of
    retrying. A sweep that is *already running* is reported first, before the
    cooldown is consulted: ``in_progress`` comes back true with ``started_at``,
    ``started_by`` and ``seconds_ago``, because a running sweep's own rows appear
    only as each supplier finishes, so thirty seconds in it has left nothing for
    the cooldown to find. That is what stops a timed-out retry from opening every
    browser a second time — and it covers ``background=True`` as well: that is
    the form a client with a short per-call budget is told to use, so it is the
    one such a client retries with, and a second detached worker would open every
    browser again. ``force=True`` sweeps regardless.
    """
    app = _get_app()

    if not force:
        # A sweep already running is the more specific answer, and checked before
        # either path below starts one: its own rows land only as each supplier
        # finishes, so the cooldown cannot see it yet.
        running = app.sweep_state()
        if running["in_progress"]:
            return {
                "cached": False,
                "in_progress": True,
                "started_at": running["started_at"],
                "started_by": running["started_by"],
                "seconds_ago": running["seconds_ago"],
                "results": [],
                "note": (
                    f"A sweep is already running (started {running['seconds_ago']}s ago "
                    f"by {running['started_by']}); nothing new was started. Read "
                    "current_prices for what has landed so far, or pass force=true to "
                    "sweep anyway."
                ),
            }

        if not background:
            # The cooldown is deliberately not consulted for a background request:
            # that form is a deliberate ask from a client that knows a sweep is
            # slow, not the accidental repeat the cooldown exists to absorb.
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

    if background:
        started = app.start_background_sweep(
            started_by="mcp", postcode=postcode or load_contact().postcode
        )
        return {
            "cached": False,
            "in_progress": True,
            "job_id": started["job_id"],
            "state": started["state"],
            "started_at": started["started_at"],
            "started_by": started["started_by"],
            "total": started["total"],
            "note": (
                "The sweep is running as its own process and will outlive this "
                f"session. Poll refresh_status('{started['job_id']}') for progress "
                "and results, or read current_prices for the prices on record."
            ),
        }

    results = app.quote_all(
        postcode=postcode or load_contact().postcode,
        prefer_browser=True,
        started_by="mcp",
    )
    observed = [row["observed_at"] for row in results if row.get("observed_at")]
    return {
        "cached": False,
        "cooldown_minutes": REFRESH_COOLDOWN_MINUTES,
        "refreshed_at": max(observed) if observed else None,
        "results": results,
    }


@mcp.tool(title="Refresh status", annotations=_READ_ONLY)
def refresh_status(job_id: str | None = None) -> dict[str, Any]:
    """Report a background sweep's progress and results.

    Pass the ``job_id`` that ``refresh_prices(background=true)`` returned, or
    omit it for the most recent job. ``state`` is ``running``, ``finished`` or
    ``failed``; ``done`` of ``total`` says how many suppliers have come back, so
    a sweep in flight is legible rather than a black box. ``failed`` carries the
    ``error``, and ``finished`` carries the same per-supplier ``results`` a
    foreground call returns.

    ``stale`` is the honest answer when a job still says ``running`` but nothing
    has updated it for longer than any sweep takes: its worker is gone, so
    waiting for it is waiting for a process that is not there.

    ``found: false`` means there is no such job — a mistyped id, or one asked for
    before the sweep it names was ever started.
    """
    return _get_app().refresh_job_status(job_id)


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
