"""Supplier-specific connectors for heating oil quote collection."""

from oilwatch.connectors.suppliers.homefuels_direct import HomeFuelsDirectConnector
from oilwatch.connectors.suppliers.valueoils import ValueOilsConnector
from oilwatch.connectors.suppliers.oilfast import OilfastConnector
from oilwatch.connectors.suppliers.rix import RixConnector
from oilwatch.connectors.suppliers.regency_oils import RegencyOilsConnector
from oilwatch.connectors.suppliers.scottish_fuels import ScottishFuelsConnector
from oilwatch.connectors.suppliers.brogan_fuels import BroganFuelsConnector
from oilwatch.connectors.suppliers.telephone import TelephoneQuoteScript
from oilwatch.connectors.suppliers.boilerjuice import BoilerJuiceBrowserConnector
from oilwatch.connectors.suppliers.fueltool import FueltoolConnector
from oilwatch.connectors.suppliers.fuelsoft import FuelsoftConnector
from oilwatch.connectors.suppliers.highland_fuels import HighlandFuelsConnector
from oilwatch.connectors.suppliers.rix_browser import RixBrowserConnector

# Browser-based connectors (require Playwright)
from oilwatch.connectors.suppliers.scottish_fuels_browser import ScottishFuelsBrowserConnector
from oilwatch.connectors.suppliers.valueoils_browser import ValueOilsBrowserConnector
from oilwatch.connectors.suppliers.homefuels_direct_browser import HomeFuelsDirectBrowserConnector


def get_supplier_connector(website: str, prefer_browser: bool = False):
    """Get a supplier-specific connector based on website domain.

    By default only HTTP-based connectors are used: they are fast, deterministic
    and work against server-rendered pages (e.g. ValueOils, HomeFuels Direct,
    Fueltool). Browser connectors (Playwright) are opt-in via ``prefer_browser``
    and cover JavaScript/login-heavy suppliers (BoilerJuice, Fuelsoft forms).
    """
    domain = website.lower()

    http_connectors = {
        "homefuelsdirect.co.uk": HomeFuelsDirectConnector(),  # Live price scraping
        "valueoils.com": ValueOilsConnector(),
        "fueltool.co.uk": FueltoolConnector(),  # UK-average benchmark
        "highlandfuels.co.uk": HighlandFuelsConnector(),  # IQO XML quote API
        "oilfast.co.uk": OilfastConnector(),
        "rix.co.uk": RixConnector(),
        "regencyoils.com": RegencyOilsConnector(),
        "scottishfuels.co.uk": ScottishFuelsConnector(),
        "brogans.co.uk": BroganFuelsConnector(),
    }

    browser_connectors = {
        "scottishfuels.co.uk": ScottishFuelsBrowserConnector(),
        "valueoils.com": ValueOilsBrowserConnector(),
        "boilerjuice.com": BoilerJuiceBrowserConnector(),
        "rix.co.uk": RixBrowserConnector(),
        # Fuelsoft WebForms platform (Connon Bros, Johnson Oils, Regency Oils).
        # No HTTP connector exists, so these are used even without --browser.
        "fuelsoft.co.uk": FuelsoftConnector(),
        "johnstonfuels.co.uk": FuelsoftConnector(),
        "regencyoils.com": FuelsoftConnector(),
        # HomeFuels Direct renders prices via JavaScript, but the HTTP connector
        # still attempts a scrape and falls back to a manual quote when nothing
        # parseable is present.
        "homefuelsdirect.co.uk": HomeFuelsDirectBrowserConnector(),
    }

    # Domains whose browser connector is unreliable (SSL/fill timeouts). Their
    # HTTP connector works, so it must win even when --browser is requested.
    http_preferred_domains = {"valueoils.com", "homefuelsdirect.co.uk"}

    if prefer_browser and not any(d in domain for d in http_preferred_domains):
        ordered = (browser_connectors, http_connectors)
    else:
        # HTTP first: the default, and forced for the domains above where the
        # browser connector is broken. Browser connectors are otherwise opt-in.
        ordered = (http_connectors, browser_connectors) if prefer_browser else (http_connectors,)

    for connectors in ordered:
        for domain_key, connector in connectors.items():
            if domain_key in domain:
                return connector

    return None


def get_telephone_script():
    """Get the telephone quote script tool."""
    return TelephoneQuoteScript()


__all__ = [
    "get_supplier_connector",
    "get_telephone_script",
    "HomeFuelsDirectConnector",
    "ValueOilsConnector",
    "OilfastConnector",
    "RixConnector",
    "RegencyOilsConnector",
    "ScottishFuelsConnector",
    "BroganFuelsConnector",
    "TelephoneQuoteScript",
    "BoilerJuiceBrowserConnector",
    "FueltoolConnector",
    "FuelsoftConnector",
    "HighlandFuelsConnector",
    "RixBrowserConnector",
    "ScottishFuelsBrowserConnector",
    "ValueOilsBrowserConnector",
    "HomeFuelsDirectBrowserConnector",
]
