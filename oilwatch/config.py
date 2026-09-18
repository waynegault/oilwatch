from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The checkout that holds ``config/`` and ``data/``. Derived from this file
#: rather than the process working directory: the MCP server is spawned by
#: another program and does not inherit the repository as its cwd, so anything
#: resolved from ``Path.cwd()`` silently came back empty — a registration note
#: with a blank email, a ``""`` postcode, and a credential store looked for in
#: the wrong directory.
CHECKOUT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(slots=True)
class HomeConfig:
    label: str
    latitude: float | None = None
    longitude: float | None = None


@dataclass(slots=True)
class SchedulerConfig:
    discovery_interval_hours: int = 168
    quote_interval_hours: int = 24
    email_monitor_interval_hours: int = 24
    #: The supplier-email sweep only runs inside this local-time window. Heating
    #: oil suppliers are shut at weekends and overnight, so a sweep then can only
    #: find an inbox that the next in-window run reads anyway.
    email_monitor_start_hour: int = 8
    email_monitor_end_hour: int = 18
    email_monitor_days: str = "mon-fri"


@dataclass(slots=True)
class Settings:
    database_path: Path
    chart_path: Path
    time_series_chart_path: Path
    home: HomeConfig
    #: Delivery postcode; identity falls back to this when contact.json has none.
    default_postcode: str = ""
    radius_miles: int = 50
    quote_quantity_liters: int = 1000
    # How many suppliers `quote-all` quotes at once. Each browser quote launches
    # its own browser, so this caps the fan-out: there is no per-supplier rate
    # limiting, and a wide burst risks the bot heuristics the connectors already
    # work around. 1 restores the strictly sequential behaviour.
    quote_max_workers: int = 4
    # A supplier with no successful quote inside this window counts as having no
    # current price, so 2007-2025 spreadsheet history can't win the comparison.
    max_quote_age_days: int = 30
    currency: str = "GBP"
    search_queries: list[str] = field(default_factory=list)
    #: Domains never to treat as a supplier. Read from the *supplier register*
    #: rather than settings.json: it is policy about suppliers, and it is
    #: version controlled, where settings.json is not (that file holds the
    #: owner's personal values and operational settings — address, postcode,
    #: credentials, the freshness window, the order quantity — but no supplier
    #: policy).
    excluded_domains: list[str] = field(default_factory=list)
    # Supplier keys the CLI signs in to, kept here rather than as literals in the
    # CLI.
    login_urls: dict[str, str] = field(default_factory=dict)
    # Public OAuth client id for the mailbox grant (not a secret — it already
    # appears in monitor_email.bat). Kept here so the CLI works without needing
    # that script purely to set one environment variable.
    microsoft_client_id: str = ""
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_settings(root: Path | None = None) -> Settings:
    base = root or CHECKOUT_ROOT
    data = _read_json(base / "config" / "settings.json")
    registry = load_supplier_registry(base)
    return Settings(
        database_path=base / data["database_path"],
        chart_path=base / data["chart_path"],
        time_series_chart_path=base / data.get("time_series_chart_path", "data/oilwatch-time-series.png"),
        home=HomeConfig(**data["home"]),
        default_postcode=data.get("default_postcode", ""),
        radius_miles=data.get("radius_miles", 50),
        quote_quantity_liters=data.get("quote_quantity_liters", 1000),
        quote_max_workers=data.get("quote_max_workers", 4),
        max_quote_age_days=data.get("max_quote_age_days", 30),
        currency=data.get("currency", "GBP"),
        search_queries=data.get("search_queries", []),
        excluded_domains=registry["excluded_domains"],
        login_urls=data.get("login_urls", {}),
        microsoft_client_id=data.get("microsoft_client_id", ""),
        scheduler=SchedulerConfig(**data.get("scheduler", {})),
    )


def load_supplier_registry(root: Path | None = None) -> dict[str, Any]:
    """The tracked supplier register: the suppliers, and the domains to skip.

    One file holds both because both are policy about which companies this
    install treats as suppliers, and this file is version controlled where
    settings.json is not. Shape::

        {"suppliers": [ {...}, ... ], "excluded_domains": ["yell.com", ...]}

    A supplier record may also say how it is asked for a price —
    ``"quote_request": {"form": "<SUPPLIER_FORMS key>"}`` or
    ``{"phone": true}`` — which is what ``submit-requests`` reads to decide
    what to do. That is in the register rather than in code, so the list of
    suppliers to chase is reviewable and shared rather than sitting in an
    ignored settings file.

    A file that is not an object is refused rather than read as an empty
    register: a stale bare list would silently leave every supplier unimported,
    which looks exactly like an install with no suppliers yet.
    """
    base = root or CHECKOUT_ROOT
    path = base / "config" / "suppliers.json"
    if not path.exists():
        return {"suppliers": [], "excluded_domains": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(
            f"{path} holds a bare {type(data).__name__}; it must be an object with "
            "'suppliers' and 'excluded_domains' keys"
        )
    return {
        "suppliers": data.get("suppliers", []),
        "excluded_domains": data.get("excluded_domains", []),
    }

