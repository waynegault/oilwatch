from oilwatch.connectors.base import BaseConnector
from oilwatch.connectors.http_form import HTTPFormConnector
from oilwatch.connectors.manual import ManualConnector
from oilwatch.connectors.price_page import PricePageConnector
from oilwatch.connectors.suppliers import get_supplier_connector


def get_connector(name: str) -> BaseConnector:
    """Get a connector by type name."""
    connectors = {
        "manual": ManualConnector(),
        "price_page": PricePageConnector(),
        "http_form": HTTPFormConnector(),
    }
    try:
        return connectors[name]
    except KeyError as exc:
        raise ValueError(f"Unknown connector type: {name}") from exc


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
    if connector_type in ("manual", "price_page", "http_form"):
        return get_connector(connector_type)

    # Default to manual
    return get_connector("manual")

