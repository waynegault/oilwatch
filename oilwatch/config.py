from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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


@dataclass(slots=True)
class Settings:
    database_path: Path
    chart_path: Path
    time_series_chart_path: Path
    home: HomeConfig
    radius_miles: int = 50
    quote_quantity_liters: int = 1000
    # A supplier with no successful quote inside this window counts as having no
    # current price, so 2007-2025 spreadsheet history can't win the comparison.
    max_quote_age_days: int = 30
    currency: str = "GBP"
    search_queries: list[str] = field(default_factory=list)
    excluded_domains: list[str] = field(default_factory=list)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_settings(root: Path | None = None) -> Settings:
    base = root or Path.cwd()
    data = _read_json(base / "config" / "settings.json")
    return Settings(
        database_path=base / data["database_path"],
        chart_path=base / data["chart_path"],
        time_series_chart_path=base / data.get("time_series_chart_path", "data/oilwatch-time-series.png"),
        home=HomeConfig(**data["home"]),
        radius_miles=data.get("radius_miles", 50),
        quote_quantity_liters=data.get("quote_quantity_liters", 1000),
        max_quote_age_days=data.get("max_quote_age_days", 30),
        currency=data.get("currency", "GBP"),
        search_queries=data.get("search_queries", []),
        excluded_domains=data.get("excluded_domains", []),
        scheduler=SchedulerConfig(**data.get("scheduler", {})),
    )


def load_supplier_overrides(root: Path | None = None) -> list[dict[str, Any]]:
    base = root or Path.cwd()
    path = base / "config" / "supplier_overrides.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))

