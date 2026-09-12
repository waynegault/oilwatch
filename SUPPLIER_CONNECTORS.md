# Supplier Connectors

How OilWatch reaches each supplier: the connector a supplier domain resolves to,
the contact details the manual flows use, and how to add a connector.

No prices live here — they move daily. Run `oilwatch cheapest` (or the `cheapest`
MCP tool) for today's figures.

---

## How selection works

Two registries in `oilwatch/connectors/suppliers/__init__.py` drive everything:

- `_CONNECTORS` — public class name → the submodule that defines it, held as
  strings so a class is imported only when something asks for it.
- `_SUPPLIER_CONNECTORS` — supplier domain fragment → `(http, browser)`, either
  side `None` where that kind does not exist for the supplier.

`get_supplier_connector(website, prefer_browser=False)` matches the website's
domain against `_SUPPLIER_CONNECTORS` and returns a connector:

- By default only the **HTTP** connector is used. It is a single request against
  a server-rendered page, so it stays the default.
- **Browser** (Playwright) connectors are opt-in with `prefer_browser=True`.
- Two domains never take the browser path: `valueoils.com` and
  `homefuelsdirect.co.uk` are listed in `_HTTP_WINS_OVER_BROWSER`, because their
  browser connectors are unreliable (SSL / fill timeouts) while the HTTP one
  works.
- When a supplier is browser-only and `prefer_browser` was not requested,
  `get_supplier_connector` returns `None` and the caller falls back to a manual
  quote.

Resolution is lazy (PEP 562 module-level `__getattr__`), so an HTTP-only run never
imports the Playwright-backed modules.

## Domain → connector

| Domain fragment | HTTP connector | Browser connector |
|-----------------|----------------|-------------------|
| `homefuelsdirect.co.uk` | `HomeFuelsDirectConnector` | `HomeFuelsDirectBrowserConnector` |
| `valueoils.com` | `ValueOilsConnector` | `ValueOilsBrowserConnector` |
| `fueltool.co.uk` | `FueltoolConnector` | — |
| `highlandfuels.co.uk` | `HighlandFuelsConnector` | — |
| `oilfast.co.uk` | `OilfastConnector` | — |
| `rix.co.uk` | `RixConnector` | `RixBrowserConnector` |
| `regencyoils.com` | `RegencyOilsConnector` | `FuelsoftConnector` |
| `scottishfuels.co.uk` | `ScottishFuelsConnector` | `ScottishFuelsBrowserConnector` |
| `brogans.co.uk` | `BroganFuelsConnector` | — |
| `boilerjuice.com` | — | `BoilerJuiceBrowserConnector` |
| `fuelsoft.co.uk` | — | `FuelsoftConnector` |
| `johnstonfuels.co.uk` | — | `FuelsoftConnector` |

`FuelsoftConnector` covers every Fuelsoft-hosted supplier from one implementation
(Connon Bros, Johnson Oils, and Regency Oils' WebOrdering). `FueltoolConnector` is
a UK-average benchmark, not a local supplier.

## HTTP connectors

One request; the price is parsed from the response (HTML, or XML for Highland).

| Connector | Endpoint |
|------------|----------|
| `ValueOilsConnector` | `https://www.valueoils.com/Quote.aspx` (regional `/regions/scotland/aberdeenshire/`) |
| `HomeFuelsDirectConnector` | `https://homefuelsdirect.co.uk/home/heating-oil-prices` (Aberdeenshire page) |
| `FueltoolConnector` | `https://www.fueltool.co.uk/` |
| `HighlandFuelsConnector` | `https://iqo-highland.fuels.app/lib/getoffers.php` — IQO XML quote API |
| `OilfastConnector` | `https://oilfast.co.uk/depot/insch/` (enquiry form; no scrapable price) |
| `RixConnector` | `https://www.rix.co.uk/fuels/heating-oil` |
| `RegencyOilsConnector` | `https://www.regencyoils.com` |
| `ScottishFuelsConnector` | `https://scottishfuels.co.uk/heating-oil-in-aberdeenshire/` |
| `BroganFuelsConnector` | `https://www.brogans.co.uk` (trades as Scottish Fuels) |

## Browser connectors

Playwright drives the supplier's own quote form, sometimes behind a login
session. The shared bases are `BrowserConnector` (`connectors/browser_base.py`)
and `SyncBrowserConnector` / `sync_page` (`connectors/sync_browser.py`).

| Connector | Entry point |
|------------|-------------|
| `RixBrowserConnector` | `https://fuelquote.rix.co.uk/` |
| `FuelsoftConnector` | Fuelsoft WebOrdering forms (Connon Bros, Johnson Oils, Regency Oils) |
| `ScottishFuelsBrowserConnector` | `https://quote.scottishfuels.co.uk/quote/` (login `/customer/account/login/`) |
| `ValueOilsBrowserConnector` | `https://www.valueoils.com/regions/scotland/aberdeenshire/` |
| `HomeFuelsDirectBrowserConnector` | `https://homefuelsdirect.co.uk/home/heating-oil-prices/aberdeenshire` |
| `BoilerJuiceBrowserConnector` | `https://www.boilerjuice.com/uk/journeys/core/quote` |

Scottish Fuels needs a live login session: `/quote/` answers 302 to the account
page once it lapses, and the connector reports that rather than failing obscurely.
Re-establish it with `oilwatch login scottish_fuels`.

BoilerJuice's connector is written but not yet collecting a price.

## Manual and generic connectors

A supplier with no scrapable quote resolves to contact details plus instructions,
or to one of three generic connector types (`GENERIC_CONNECTOR_TYPES` in
`oilwatch/connectors/__init__.py`):

| `connector_type` | Connector | Use |
|------------------|-----------|-----|
| `manual` | `ManualConnector` | Contact details only; returns `manual_action_required` |
| `price_page` | `PricePageConnector` | A price read from a plain page |
| `http_form` | `HTTPFormConnector` | One form POST |

Domain matching is tried first. A supplier row may name a `connector_type`
explicitly, and an unknown type is a configuration error rather than a silent
downgrade to a manual quote.

## Contact details

Used by the manual connectors, the enquiry-form path (`oilwatch
submit-requests`) and the `phone-script` command.

| Supplier | Contact |
|----------|---------|
| Oilfast (Insch) | 01464 635999 · 03302 320 104 · insch@oilfast.co.uk · https://oilfast.co.uk/depot/insch/ |
| Rix | Aberdeen 01224 455477 · general 0800 542 4207 · montsales@rix.co.uk · sales@rix.co.uk |
| Regency Oils | 0800 838500 · https://www.regencyoils.com |
| Scottish Fuels | 0345 300 8844 · Aberdeen 01224 213 132 · info@scottishfuels.co.uk |
| Brogan Fuels | 0345 300 8844 · domestic@brogans.co.uk · https://www.brogans.co.uk |

## Telephone quote script

`TelephoneQuoteScript` (`connectors/suppliers/telephone.py`) builds call sheets and
is exposed as the `phone-script` command:

```powershell
.venv\Scripts\python -m oilwatch.cli phone-script `
  --quantity-liters 1000 `
  --postcode "AB21 0YA" `
  --name "Your Name" `
  --address "Your Address" `
  --output data/quote-calls.json
```

It emits a quick-reference card with the phone numbers above, per-supplier call
scripts and a recording checklist; `--output` writes the run to JSON.

## Adding a connector

1. Add the connector module under `oilwatch/connectors/suppliers/`.
2. Register the class in `_CONNECTORS` (public name → submodule string).
3. Add the supplier's domain row to `_SUPPLIER_CONNECTORS` as
   `(http_name, browser_name)`, using `None` for a kind that does not exist.
4. Cover it and try it:

   ```powershell
   .venv\Scripts\python -m unittest tests.test_connector_imports
   .venv\Scripts\python -m oilwatch.cli quote <supplier_id>
   ```

## File structure

```
oilwatch/connectors/
├── base.py                   # BaseConnector
├── browser_base.py           # BrowserConnector (Playwright)
├── sync_browser.py           # SyncBrowserConnector / sync_page
├── manual.py                 # ManualConnector
├── price_page.py             # PricePageConnector
├── http_form.py              # HTTPFormConnector
└── suppliers/
    ├── __init__.py           # _CONNECTORS + _SUPPLIER_CONNECTORS + selection
    ├── valueoils.py          # ValueOilsConnector
    ├── valueoils_browser.py  # ValueOilsBrowserConnector
    ├── homefuels_direct.py   # HomeFuelsDirectConnector
    ├── homefuels_direct_browser.py
    ├── fueltool.py           # FueltoolConnector
    ├── highland_fuels.py     # HighlandFuelsConnector
    ├── oilfast.py            # OilfastConnector
    ├── rix.py                # RixConnector
    ├── rix_browser.py        # RixBrowserConnector
    ├── regency_oils.py       # RegencyOilsConnector
    ├── scottish_fuels.py     # ScottishFuelsConnector
    ├── scottish_fuels_browser.py
    ├── brogan_fuels.py       # BroganFuelsConnector
    ├── boilerjuice.py        # BoilerJuiceBrowserConnector
    ├── fuelsoft.py           # FuelsoftConnector
    └── telephone.py          # TelephoneQuoteScript
```
