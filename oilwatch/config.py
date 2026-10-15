from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

#: The checkout that holds ``config/`` and ``data/``. Derived from this file
#: rather than the process working directory: the MCP server is spawned by
#: another program and does not inherit the repository as its cwd, so anything
#: resolved from ``Path.cwd()`` silently came back empty — a registration note
#: with a blank email, a ``""`` postcode, and a credential store looked for in
#: the wrong directory.
CHECKOUT_ROOT = Path(__file__).resolve().parents[1]


class _StrictConfigModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", validate_assignment=True)


class HomeConfig(_StrictConfigModel):
    label: str
    latitude: float | None = None
    longitude: float | None = None

    @model_validator(mode="after")
    def coordinates_are_complete(self) -> HomeConfig:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be configured together")
        return self


class SchedulerConfig(_StrictConfigModel):
    discovery_interval_hours: int = Field(default=168, gt=0)
    quote_interval_hours: int = Field(default=24, gt=0)
    email_monitor_interval_hours: int = Field(default=24, gt=0)
    #: The supplier-email sweep only runs inside this local-time window. Heating
    #: oil suppliers are shut at weekends and overnight, so a sweep then can only
    #: find an inbox that the next in-window run reads anyway.
    email_monitor_start_hour: int = Field(default=8, ge=0, le=23)
    email_monitor_end_hour: int = Field(default=18, ge=0, le=23)
    email_monitor_days: str = "mon-fri"


class Settings(_StrictConfigModel):
    database_path: Path
    chart_path: Path
    time_series_chart_path: Path
    home: HomeConfig
    #: Delivery postcode; identity falls back to this when contact.json has none.
    default_postcode: str = ""
    radius_miles: int = Field(default=50, gt=0)
    quote_quantity_liters: int = Field(default=1000, gt=0)
    # How many suppliers `quote-all` quotes at once. Each browser quote launches
    # its own browser, so this caps the fan-out: there is no per-supplier rate
    # limiting, and a wide burst risks the bot heuristics the connectors already
    # work around. 1 restores the strictly sequential behaviour.
    quote_max_workers: int = Field(default=4, gt=0)
    # A supplier with no successful quote inside this window counts as having no
    # current price, so 2007-2025 spreadsheet history can't win the comparison.
    max_quote_age_days: int = Field(default=30, gt=0)
    currency: str = "GBP"
    search_queries: list[str] = Field(default_factory=list)
    #: Domains never to treat as a supplier. Read from the *supplier register*
    #: rather than settings.json: it is policy about suppliers, and it is
    #: version controlled, where settings.json is not (that file holds the
    #: owner's personal values and operational settings — address, postcode,
    #: credentials, the freshness window, the order quantity — but no supplier
    #: policy).
    excluded_domains: list[str] = Field(default_factory=list)
    # Supplier keys the CLI signs in to, kept here rather than as literals in the
    # CLI.
    login_urls: dict[str, str] = Field(default_factory=dict)
    # Public OAuth client id for the mailbox grant (not a secret — it already
    # appears in monitor_email.bat). Kept here so the CLI works without needing
    # that script purely to set one environment variable.
    microsoft_client_id: str = ""
    #: How likely an unrecognised sender's mail must read as a fuel quote before
    #: the sweep names that domain in its log. `extract_ppl` is tried first
    #: because it is free; when it finds no price a model is asked, and this is
    #: the threshold its probability is held to. Cached per sender — and each
    #: verdict records the newest mail it accounted for, so a below-threshold
    #: sender is judged again for later mail rather than deciding its alert.
    #:
    #: A conservative default rather than a fitted one: no labelled sender set
    #: exists to fit it against, and the senders whose mail this decides are the
    #: ones already recognised. It is measurable anyway, because the sweep has
    #: stored a verdict per sender it judged and the supplier register labels the
    #: named ones — `python tools/fuel_mail_calibration.py` reports whether the
    #: threshold separates them, and says so when nothing sits near it.
    fuel_mail_min_probability: float = Field(default=0.8, ge=0.0, le=1.0)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)

    @field_validator(
        "database_path", "chart_path", "time_series_chart_path", mode="before"
    )
    @classmethod
    def resolve_paths(cls, value: object, info: ValidationInfo) -> object:
        if not isinstance(value, str):
            return value
        path = Path(value)
        root = (info.context or {}).get("root")
        return path if root is None or path.is_absolute() else Path(root) / path


class SupplierQuoteRequest(_StrictConfigModel):
    form: str | None = Field(default=None, min_length=1)
    no_form: Literal[True] | None = None

    @model_validator(mode="after")
    def choose_one_request_route(self) -> SupplierQuoteRequest:
        if (self.form is None) == (self.no_form is None):
            raise ValueError("quote_request must contain exactly one of form or no_form=true")
        return self


class ConnectorConfig(_StrictConfigModel):
    quote_url: str | None = None
    order_page: str | None = None
    price_regex: str | None = None
    quote_method: Literal["GET", "POST"] = "POST"
    quote_fields: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    vat_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    product_value: str | None = None
    product_sku: str | None = None


class SupplierRecord(_StrictConfigModel):
    name: str = Field(min_length=1)
    website: str = Field(min_length=1)
    status: Literal["active", "inactive", "manual_review"] = "active"
    connector_type: str = Field(default="manual", min_length=1)
    connector_config: ConnectorConfig = Field(default_factory=ConnectorConfig)
    phone: str | None = None
    email: str | None = None
    kind: Literal["supplier", "benchmark"] = "supplier"
    quote_request: SupplierQuoteRequest | None = None
    notes: str = ""


class SupplierRegistry(_StrictConfigModel):
    suppliers: list[SupplierRecord]
    excluded_domains: list[str]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_connector_config(value: object) -> dict[str, Any]:
    try:
        config = ConnectorConfig.model_validate(value)
    except ValidationError as exc:
        raise ValueError(f"Invalid connector_config: {exc}") from exc
    return config.model_dump(exclude_unset=True)


def parse_connector_config(value: str | None) -> dict[str, Any]:
    try:
        raw = json.loads(value or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid connector_config_json") from exc
    return validate_connector_config(raw)


def load_settings(root: Path | None = None) -> Settings:
    base = root or CHECKOUT_ROOT
    settings_path = base / "config" / "settings.json"
    data = _read_json(settings_path)
    if not isinstance(data, dict):
        raise ValueError(f"{settings_path} must contain a JSON object")
    if "excluded_domains" in data:
        raise ValueError(
            f"{settings_path}: excluded_domains belongs in config/suppliers.json"
        )
    registry = load_supplier_registry(base)
    data["time_series_chart_path"] = data.get(
        "time_series_chart_path", "data/oilwatch-time-series.png"
    )
    data["excluded_domains"] = registry["excluded_domains"]
    try:
        return Settings.model_validate(data, context={"root": base})
    except ValidationError as exc:
        raise ValueError(f"Invalid settings in {settings_path}: {exc}") from exc


def load_supplier_registry(root: Path | None = None) -> dict[str, Any]:
    """The tracked supplier register: the suppliers, and the domains to skip.

    One file holds both because both are policy about which companies this
    install treats as suppliers, and this file is version controlled where
    settings.json is not. Shape::

        {"suppliers": [ {...}, ... ], "excluded_domains": ["yell.com", ...]}

    A supplier record may also say how it is asked for a price —
    ``"quote_request": {"form": "<SUPPLIER_FORMS key>"}`` or
    ``{"no_form": true}`` — which is what ``submit-requests`` reads to decide
    what to do. A record with a form is driven; ``no_form`` says there is
    nothing on its site to drive, so ``submit-requests --by-email`` writes to
    the address on the record instead, and a record with neither a form nor an
    address is simply not asked. That is in the register rather than in code, so
    the list of suppliers to chase is reviewable and shared rather than sitting
    in an ignored settings file.

    A missing or malformed register fails rather than becoming an empty list:
    either condition would otherwise look exactly like an install with no
    suppliers yet.
    """
    base = root or CHECKOUT_ROOT
    path = base / "config" / "suppliers.json"
    if not path.exists():
        raise FileNotFoundError(f"Supplier registry does not exist: {path}")
    data = _read_json(path)
    if not isinstance(data, dict):
        raise ValueError(
            f"{path} holds a bare {type(data).__name__}; it must be an object with "
            "'suppliers' and 'excluded_domains' keys"
        )
    try:
        registry = SupplierRegistry.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Invalid supplier registry in {path}: {exc}") from exc
    return {
        "suppliers": [
            supplier.model_dump(exclude_unset=True) for supplier in registry.suppliers
        ],
        "excluded_domains": registry.excluded_domains,
    }
