from __future__ import annotations

from oilwatch.connectors.base import BaseConnector
from oilwatch.connectors.http_form import HTTPFormConnector
from oilwatch.connectors.manual import ManualConnector
from oilwatch.connectors.price_page import PricePageConnector
from oilwatch.connectors.suppliers import get_supplier_connector
from oilwatch.logging_setup import get_logger

log = get_logger("connectors")

# Generic (non supplier-specific) connector types a supplier row may name.
GENERIC_CONNECTOR_TYPES = ("manual", "price_page", "http_form")


def get_connector(name: str) -> BaseConnector:
    """Get a connector by type name, or raise for a type it does not know."""
    factories = {
        "manual": ManualConnector,
        "price_page": PricePageConnector,
        "http_form": HTTPFormConnector,
    }
    if name not in factories:
        raise ValueError(f"Unknown connector type: {name}")
    return factories[name]()


def get_connector_for_supplier(supplier: dict, prefer_browser: bool = False) -> BaseConnector:
    """
    Get the appropriate connector for a supplier.

    First tries to match by website domain to supplier-specific connectors.
    Falls back to generic connector type or manual. Set ``prefer_browser`` to
    opt in to Playwright-based connectors for JavaScript-heavy suppliers.
    """
    # Try to match by website domain first (supplier-specific connectors)
    website = supplier.get("website", "")
    supplier_connector = get_supplier_connector(website, prefer_browser=prefer_browser)
    if supplier_connector:
        return supplier_connector

    # Check for explicit connector type
    connector_type = supplier.get("connector_type", "")
    if connector_type in GENERIC_CONNECTOR_TYPES:
        return get_connector(connector_type)
    if connector_type:
        # A set-but-unknown type is a configuration error, not a reason to
        # silently downgrade the supplier to a manual quote.
        raise ValueError(
            f"Unknown connector_type {connector_type!r} for supplier "
            f"{supplier.get('name') or supplier.get('id')}"
        )

    log.info(
        "No supplier-specific connector for %r (website=%r); using manual",
        supplier.get("name") or supplier.get("id"),
        website,
    )
    return get_connector("manual")
