"""Supplier-specific connectors for heating oil quote collection.

Connector classes are resolved lazily: this package is imported by the HTTP-only
quote path, and importing every supplier module up front would pull in Playwright
(the browser connectors) for a run that never opens a browser. Module-level
``__getattr__`` (PEP 562) keeps ``from oilwatch.connectors.suppliers import X``
working while importing only the connector that is actually selected.
"""

from __future__ import annotations

import importlib

# Public connector name -> the submodule that defines it. Held as strings so a
# name is only imported when something asks for it.
_CONNECTORS = {
    "HomeFuelsDirectConnector": "homefuels_direct",
    "ValueOilsConnector": "valueoils",
    "OilfastConnector": "oilfast",
    "RixConnector": "rix",
    "RegencyOilsConnector": "regency_oils",
    "ScottishFuelsConnector": "scottish_fuels",
    "BroganFuelsConnector": "brogan_fuels",
    "TelephoneQuoteScript": "telephone",
    "BoilerJuiceBrowserConnector": "boilerjuice",
    "FueltoolConnector": "fueltool",
    "FuelsoftConnector": "fuelsoft",
    "HighlandFuelsConnector": "highland_fuels",
    "RixBrowserConnector": "rix_browser",
    "ScottishFuelsBrowserConnector": "scottish_fuels_browser",
    "ValueOilsBrowserConnector": "valueoils_browser",
    "HomeFuelsDirectBrowserConnector": "homefuels_direct_browser",
}

# One row per supplier domain: (HTTP connector, browser connector), either of
# which is None when that kind does not exist for the supplier. The first domain
# fragment found (in insertion order) wins.
_SUPPLIER_CONNECTORS: dict[str, tuple[str | None, str | None]] = {
    "homefuelsdirect.co.uk": ("HomeFuelsDirectConnector", "HomeFuelsDirectBrowserConnector"),
    "valueoils.com": ("ValueOilsConnector", "ValueOilsBrowserConnector"),
    "fueltool.co.uk": ("FueltoolConnector", None),  # UK-average benchmark
    "highlandfuels.co.uk": ("HighlandFuelsConnector", None),  # IQO XML quote API
    "oilfast.co.uk": ("OilfastConnector", None),
    "rix.co.uk": ("RixConnector", "RixBrowserConnector"),
    "regencyoils.com": ("RegencyOilsConnector", "FuelsoftConnector"),
    "scottishfuels.co.uk": ("ScottishFuelsConnector", "ScottishFuelsBrowserConnector"),
    "brogans.co.uk": ("BroganFuelsConnector", None),
    # Browser-only: Fuelsoft WebOrdering (Connon Bros, Johnson Oils) and
    # BoilerJuice expose no HTTP-scrapable price, so these resolve only when
    # ``prefer_browser=True``; otherwise the caller falls back to a manual quote.
    "boilerjuice.com": (None, "BoilerJuiceBrowserConnector"),
    "fuelsoft.co.uk": (None, "FuelsoftConnector"),
    "johnstonfuels.co.uk": (None, "FuelsoftConnector"),
}

# Domains whose browser connector is unreliable (SSL/fill timeouts). Their HTTP
# connector works, so it must win even when --browser is requested.
_HTTP_WINS_OVER_BROWSER = frozenset({"valueoils.com", "homefuelsdirect.co.uk"})


def __getattr__(name: str):
    module = _CONNECTORS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f"{__name__}.{module}"), name)


def _build(name: str):
    """Import and instantiate a connector by its public name."""
    return __getattr__(name)()


def _match(domain: str) -> tuple[str | None, str | None]:
    for fragment, pair in _SUPPLIER_CONNECTORS.items():
        if fragment in domain:
            return pair
    return None, None


def get_supplier_connector(website: str, prefer_browser: bool = False):
    """Get a supplier-specific connector based on website domain.

    By default only HTTP-based connectors are used: they are fast, deterministic
    and work against server-rendered pages (e.g. ValueOils, HomeFuels Direct,
    Fueltool). Browser connectors (Playwright) are opt-in via ``prefer_browser``
    and cover JavaScript/login-heavy suppliers (BoilerJuice, Fuelsoft forms);
    with ``prefer_browser=True`` the browser connector is preferred, except for
    the domains in ``_HTTP_WINS_OVER_BROWSER``.

    Only the matched connector is imported, so an HTTP-only call never imports
    the Playwright-backed modules. Returns ``None`` when no domain matches.
    """
    domain = website.lower()
    http_name, browser_name = _match(domain)

    if prefer_browser and not any(d in domain for d in _HTTP_WINS_OVER_BROWSER):
        chosen = browser_name or http_name
    else:
        chosen = http_name

    return _build(chosen) if chosen else None


def get_telephone_script():
    """Get the telephone quote script tool."""
    return _build("TelephoneQuoteScript")


__all__ = [
    "get_supplier_connector",
    "get_telephone_script",
    *_CONNECTORS,
]
