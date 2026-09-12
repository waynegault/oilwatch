"""Supplier email parsing: which senders count, and what price they state.

Suppliers that only quote by form/email (Gleaner Oils, Oilfast, Highland Fuels,
Regency Oils, ValueOils) reply by email with their price. This module holds the two pieces
of that knowledge which are independent of how the mailbox is read:

* ``SUPPLIER_DOMAINS`` — the reply domains seen, mapped to the supplier website
  fragment the database matches on.
* ``extract_ppl`` — the price parser, which knows the formats those suppliers
  actually use.

Reading the mailbox is ``graph_email.py`` (Microsoft Graph / OAuth2). There is no
IMAP monitor here any more: Outlook disabled basic password authentication, so
the previous ``EmailMonitor`` could not connect to a real mailbox and no entry
point called it. ``MICROSOFT_PASSWORD``, ``MICROSOFT_EMAIL``,
``MICROSOFT_IMAP_SERVER`` and ``MICROSOFT_IMAP_PORT`` are not read by anything.
"""

from __future__ import annotations

import re

from oilwatch.pricing import DOMESTIC_VAT_RATE, pence_to_pounds

# Map an email sender domain to the supplier website fragment used by the DB.
SUPPLIER_DOMAINS = {
    "gleaner.co.uk": "gleaner.co.uk",
    "oilfast.co.uk": "oilfast.co.uk",
    "highlandfuels.co.uk": "highlandfuels.co.uk",
    "regencyoils.com": "regencyoils.com",
    "regencyoils.co.uk": "regencyoils.com",  # replies arrive from the .co.uk domain
    "scottishfuels.co.uk": "scottishfuels.co.uk",
    "certasenergy.co.uk": "scottishfuels.co.uk",  # Scottish Fuels = Certas Energy
    "rix.co.uk": "rix.co.uk",
    "brogans.co.uk": "brogans.co.uk",
    "connon-oils.co.uk": "connon",  # Connon Bros/Oils (Fuelsoft, website connon.fuelsoft.co.uk)
    "connon.fuelsoft.co.uk": "connon",
    # Senders whose mail carries prices or discount codes. ValueOils was missing,
    # which meant its emails were skipped before anything was parsed — including
    # the discount offers it sends.
    "valueoils.com": "valueoils.com",
    "homefuelsdirect.co.uk": "homefuelsdirect.co.uk",
    # BoilerJuice is a broker rather than a discovered supplier (settings list
    # boilerjuice.com in excluded_domains), but it emails quotes and its total
    # carries a service charge the headline PPL omits. Quotes come from
    # boilerjuice.com, mail-outs from e.boilerjuice.com — subdomains are matched
    # by supplier_fragment_for(), so only the registrable domain is listed.
    "boilerjuice.com": "boilerjuice.com",
    "crownoil.co.uk": "crownoil.co.uk",
    "nationwidefuels.co.uk": "nationwidefuels.co.uk",
    "compassfuels.co.uk": "compassfuels.co.uk",
}


def supplier_fragment_for(domain: str) -> str | None:
    """The supplier website fragment for a sender domain, or ``None``.

    Suppliers send from subdomains as well as their registrable domain:
    BoilerJuice quotes come from ``boilerjuice.com``, but its mail-outs come from
    ``e.boilerjuice.com``. An exact-lookup map silently skips those, so match the
    longest known domain the sender equals or sits under — on a dot boundary, so
    ``notvalueoils.com`` cannot masquerade as ``valueoils.com``.
    """
    if not domain:
        return None
    domain = domain.lower()
    matched = max(
        (known for known in SUPPLIER_DOMAINS if domain == known or domain.endswith("." + known)),
        key=len,
        default=None,
    )
    return SUPPLIER_DOMAINS[matched] if matched else None


# Price patterns found in supplier reply emails.
PPL_PATTERNS = [
    r"(\d+(?:\.\d{1,2})?)\s*p(?:ence)?\s*(?:per\s*/?\s*litre|/l)\b",
    r"(\d+(?:\.\d{1,2})?)\s*p\s*\(?excl?\.?\s*vat\)?",
    r"price\s*per\s*litre[^£\n]*[£]?\s*(\d+(?:\.\d{1,2})?)\s*p",
    r"£\s?(\d+(?:\.\d{1,2})?)\s*(?:per\s*/?\s*litre|/l)\b",
    # "107.50PPL" — Highland Fuels states the unit price as pence per litre with
    # a PPL suffix and no "per litre" wording.
    r"(\d+(?:\.\d{1,2})?)\s*ppl\b",
    # ValueOils quotes state the unit price beside the option total as bare
    # pence: "Standard Delivery ... 102.90p £1,101.45". There is no "per litre"
    # wording and no "Excl. VAT" suffix, so every pattern above misses it — which
    # left the message unprocessed and stuck in the inbox.
    r"(\d{2,3}\.\d{1,2})p\s*£",
]

# BoilerJuice states an inclusive total for a stated quantity rather than a bare
# unit price: "Get 1,000 litres of kerosene 28 for £1588.99".
_TOTAL_FOR_QUANTITY = re.compile(
    r"Get\s+(?P<litres>[\d,]+)\s*litres?\b[^£]{0,80}?£\s*(?P<total>[\d,]+\.\d{2})",
    re.IGNORECASE,
)


def extract_ppl(text: str) -> float | None:
    """Extract the price per litre (GBP, ex-VAT) from an email body.

    Handles the three formats actually seen in supplier replies:

    * Fuelsoft emails list the unit price as ``£1.0481`` (a £ value with four
      decimals; totals use only two).
    * Rix writes ``Price per litre 110.35`` (pence, no unit suffix).
    * Scottish Fuels writes ``Price Per Litre (Excl. VAT): 101.03p`` (pence).
    * ValueOils quotes list the options as ``102.90p £1,101.45`` — the unit
      price as bare pence beside the total, with neither wording nor VAT suffix.
    * BoilerJuice quotes state an inclusive total for a quantity
      (``Get 1,000 litres ... for £1588.99``). That total is used, not the
      headline PPL, because BoilerJuice's service charge lives in the total.

    Returns ``None`` if no price is found.
    """
    # BoilerJuice's own service charge sits in the stated total and nowhere
    # else, so the accompanying "Price per litre ... ppl" understates what you
    # actually pay. Prefer the total, returning an ex-VAT figure so the caller's
    # VAT step reproduces the supplier's own number.
    total_match = _TOTAL_FOR_QUANTITY.search(text)
    if total_match:
        litres = float(total_match.group("litres").replace(",", ""))
        if litres > 0:
            inc_vat = float(total_match.group("total").replace(",", "")) / litres
            return round(inc_vat / (1 + DOMESTIC_VAT_RATE), 4)

    # Fuelsoft-style £ per litre with exactly 4 decimals.
    pounds4 = re.findall(r"£\s?(\d+\.\d{4})\b", text)
    if pounds4:
        return round(min(float(v) for v in pounds4), 4)

    # "Price per litre ..." — the number is pence in the replies seen so far.
    m = re.search(r"price\s*per\s*litre[^\d£]{0,60}?(\d+(?:\.\d{1,2})?)", text, re.IGNORECASE)
    if m:
        return pence_to_pounds(m.group(1))

    for pattern in PPL_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            raw = m.group(1)
            value = float(raw)
            # pence vs pounds heuristic: >100 => pence
            if value > 100:
                return pence_to_pounds(value)
            if value < 10:
                return round(value, 4)
            # 10..100 is ambiguous; treat as pence (oil ~90-160p)
            return pence_to_pounds(value)
    return None
