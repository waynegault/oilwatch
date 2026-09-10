"""Central price normalisation for OilWatch.

Every connector must funnel its raw scraped price through this module so that
quotes are stored on a single, comparable basis.

Convention (enforced across the whole app):

* ``price_per_liter`` is stored in **GBP per litre, inclusive of VAT**.
* Domestic heating oil (kerosene / 28-second burning oil) is subject to the
  reduced UK VAT rate of 5%. This is the default applied everywhere so that a
  "cheapest supplier" comparison is apples-to-apples.
* ``total_price`` is derived as ``price_per_liter * quantity_liters``.

Connectors that scrape a price *excluding* VAT must call :func:`apply_vat`
before producing a ``QuoteResult``. Connectors that scrape an inclusive price
must simply pass the inclusive value through :func:`normalise_price_per_litre`.
"""

from __future__ import annotations

DOMESTIC_VAT_RATE = 0.05


def normalise_price_per_litre(raw: str | int | float | None) -> float | None:
    """Normalise a raw per-litre price to GBP per litre.

    Heating oil in the UK is quoted in pence per litre (e.g. ``155.80``) or,
    less commonly, in pounds per litre (e.g. ``1.558``). Anything above 100 is
    treated as pence and divided by 100; everything else is assumed to already
    be pounds. This covers the realistic range of pence (roughly 100-300p) and
    pounds (roughly £0.5-£2.0) without ambiguity.

    Returns ``None`` for empty/unparseable input.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.replace(",", "").replace("£", "").strip()
        if not raw:
            return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value > 100:
        value /= 100
    return round(value, 4)


def pence_to_pounds(pence: str | int | float) -> float:
    """Convert an explicit pence-per-litre value to GBP per litre.

    Use this when the source *declares* the unit (e.g. ``103.90p`` or
    ``99.15 pence``) rather than :func:`normalise_price_per_litre`, whose
    >100 heuristic cannot tell ``99.15`` (pence) apart from ``99.15`` (pounds).
    """
    value = float(str(pence).replace(",", "").strip())
    return round(value / 100, 4)


def apply_vat(ex_vat_price: float, rate: float = DOMESTIC_VAT_RATE) -> float:
    """Return an ex-VAT price converted to inclusive of VAT, rounded to 4 dp."""
    return round(ex_vat_price * (1 + rate), 4)


def inclusive_total(price_per_liter_inclusive: float, quantity_liters: int) -> float:
    """Return the inclusive total cost for a quantity, rounded to 2 dp."""
    return round(price_per_liter_inclusive * quantity_liters, 2)
