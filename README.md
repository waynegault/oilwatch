# OilWatch

**Domestic Heating Oil Price Tracker for Aberdeenshire, Scotland**

OilWatch automatically tracks heating oil prices from local suppliers, finds the cheapest option, and exposes everything through both a CLI and MCP server for AI agent integration.

---

## What This Codebase Does

OilWatch solves the problem of finding the best price for domestic heating oil (kerosene) in the Hatton of Fintray, Aberdeenshire area (AB21 0YA).

### Core Capabilities

| Feature | Description | Status |
|---------|-------------|--------|
| **Supplier Discovery** | Finds local suppliers via web search, filters by 50-mile radius | ✅ Complete |
| **Automated Quotes** | HTTP scraping + browser automation extract live prices | ✅ 8 suppliers live |
| **Price Comparison** | Compares all suppliers, identifies cheapest option | ✅ Complete |
| **Historical Tracking** | Stores quote history for trend analysis | ✅ Complete |
| **Market Charts** | Generates price movement charts over time | ✅ Complete |
| **MCP Server** | Exposes all functions as AI agent tools | ✅ Complete |
| **Phone Scripts** | Generates call scripts for manual supplier quotes | ✅ Complete |

### Current Automation Status

Verified 2026-09-10: a single `quote-all` run collected live prices from **8
suppliers** through named connectors. Prices move daily, so this records *how*
each supplier is reached rather than what it charges — run `oilwatch cheapest`
for today's figures.

| Supplier | Reached by | Notes |
|----------|-----------|-------|
| **ValueOils** | HTTP scrape (`valueoils_auto`) | Regional price table |
| **HomeFuels Direct** | HTTP scrape (`homefuels_live_price`) | Live price element |
| **Fueltool** | HTTP scrape (`fueltool`) | UK-average benchmark, not a local supplier |
| **Rix** | Browser (`rix_browser`) | Remix quote tool |
| **Regency Oils** | Browser (`fuelsoft`) | Fuelsoft WEBPLUS |
| **Connon Bros** | Browser (`fuelsoft`) | Fuelsoft WebOrdering |
| **Johnson Oils** | Browser (`fuelsoft`) | Fuelsoft WebOrdering |
| **Highland Fuels** | HTTP scrape (`highland_fuels`) | IQO XML quote API |
| **Scottish Fuels** | Browser (`scottish_fuels_browser`) + email replies | Needs a live login session — see Known Issues |
| **Oilfast Insch, Turriff, Carnegie, Brogan** | Phone / email | No scrapable quote; see `oilwatch phone-script` |

Brogan Fuels trades as part of Scottish Fuels, so the Scottish Fuels figure
covers it. Where a supplier replies to an enquiry by email, `oilwatch
monitor-email` records the price and removes the message from the inbox.

---

## Quick Start

### Installation

```powershell
# From the repository root (the folder containing pyproject.toml)
cd <path-to-oilwatch>

# Create and activate the virtual environment (Python 3.12+)
python -m venv .venv
.venv\Scripts\activate

# Install the package with its dev extras
pip install -e .[dev]

# Download the Chromium build the browser connectors use
python -m playwright install chromium
```

### First Run

```powershell
# Initialize database
python -m oilwatch.cli init

# Discover local suppliers
python -m oilwatch.cli discover

# Collect quotes from all suppliers
python -m oilwatch.cli quote-all --postcode "AB21 0YA"

# Find cheapest supplier
python -m oilwatch.cli cheapest

# Generate price chart
python -m oilwatch.cli chart
```

---

## CLI Commands

### Price Commands

```powershell
# Get quotes from ALL suppliers (automated + manual instructions)
python -m oilwatch.cli quote-all --postcode "AB21 0YA"

# Get quote from SINGLE supplier (by ID number)
python -m oilwatch.cli quote 2 --postcode "AB21 0YA"  # ValueOils
python -m oilwatch.cli quote 3 --postcode "AB21 0YA"  # HomeFuels Direct

# Show CHEAPEST supplier with average and variance
python -m oilwatch.cli cheapest

# Show cheapest supplier + price direction + buy/hold recommendation
python -m oilwatch.cli status

# Generate PRICE CHART showing market trends
python -m oilwatch.cli chart
```

### Supplier Commands

```powershell
# List ALL suppliers with contact details
python -m oilwatch.cli suppliers

# List suppliers including inactive ones
python -m oilwatch.cli suppliers --include-inactive

# Discover NEW suppliers via web search
python -m oilwatch.cli discover
```

### Manual Quote Commands

```powershell
# Generate PHONE CALL SCRIPTS for manual suppliers
python -m oilwatch.cli phone-script --postcode "AB21 0YA" --name "Your Name"

# Export call sheets to JSON for tracking
python -m oilwatch.cli phone-script --postcode "AB21 0YA" --name "Your Name" --output data\call-sheets.json
```

### Purchase Records

Recording a purchase is separate from buying: nothing here drives a browser or
contacts a supplier. You buy by phone or on the supplier's own site, then write
down what happened, so the market data is not the only thing the database knows.

```powershell
# Record a purchase you have made (supplier by name, name fragment, or id)
python -m oilwatch.cli record-purchase "Scottish Fuels" --price-per-liter 1.0894 --code autumn25

# If you know the total rather than the unit price, give that instead
python -m oilwatch.cli record-purchase "Scottish Fuels" --total 1089.40 --litres 1000

# List recorded purchases, newest first
python -m oilwatch.cli purchases
```

A name that matches several suppliers is refused rather than guessed, so a
purchase cannot be filed against the wrong one.

### Automation Commands

```powershell
# Run continuous scheduler (daily quotes, weekly discovery)
python -m oilwatch.cli schedule --postcode "AB21 0YA"

# Start MCP server for AI agent integration (it also starts automatically at logon)
python -m oilwatch.mcp_server
```

### Utility Commands

```powershell
# Initialize database and import supplier credentials
python -m oilwatch.cli init

# Discover APIs on supplier websites (for development)
python -m oilwatch.cli api-discover --url "https://www.valueoils.com"

# Register accounts on supplier websites
python -m oilwatch.cli register --postcode "AB21 0YA"
```

---

## How an AI Agent Should Use This

OilWatch exposes all functionality through an **MCP (Model Context Protocol) server**, enabling AI agents to autonomously track oil prices and find the best deals.

### MCP Tools Available

The server exposes nine tools, all read-mostly. There is **no** ordering tool:
`place_order` does not exist, and an agent must never claim an order was placed.
Recording a purchase is a deliberate CLI act by the owner
(`oilwatch record-purchase`); agents can only read them back via `purchases`.

| Tool | Description | Parameters |
|------|-------------|------------|
| `list_suppliers` | Suppliers on record | None |
| `current_prices` | Latest price per supplier (£/L inc. VAT) with `valid_until`; ignores quotes older than `max_quote_age_days` (`cheapest`/`status` name those it drops) | None |
| `cheapest` | Cheapest supplier + market average and variance, including how long that offer stands (`valid_until`) and any `excluded_suppliers` the age window dropped | None |
| `purchases` | Purchases already recorded, newest first, with totals and discount codes | None |
| `status` | Snapshot + price trend + buy/hold recommendation, including the last purchase | None |
| `chart` | Market summary chart; returns a file path | None |
| `time_series_chart` | Per-supplier prices with Brent crude on a second axis; returns a path | None |
| `refresh_prices` | Scrape fresh quotes from all suppliers — **slow** (minutes, browser automation) | `postcode: str` |
| `update_brent` | Fetch the latest Brent crude daily series from the EIA | None |

Each tool carries MCP `ToolAnnotations`, so a client can distinguish a safe read
(`readOnlyHint`) from a slow, world-touching scrape (`openWorldHint`) and gate
approvals accordingly.

### AI Agent Workflow

```python
# Example: an agent checks prices.

# 1. Read the current market position and recommendation
snapshot = status()

# 2. Refresh only when the stored prices look old — this takes minutes
if snapshot["trend"]["direction"] == "insufficient_data":
    refresh_prices(postcode="AB21 0YA")
    snapshot = status()

# 3. Report
cheapest = snapshot["market_snapshot"]["cheapest_supplier"]
if cheapest is None:
    return "No current quotes — run refresh_prices first."

# 4. Recency matters: the cheapest entry carries its observation date, so check
#    it before presenting a price as today's.
return f"{cheapest['name']} at £{cheapest['price_per_liter']}/L (observed {cheapest['observed_at']})"
```

### Starting the MCP Server

The server starts automatically at logon (a per-user Startup entry that runs
`start_mcp_server.bat`, binding `0.0.0.0:8000`). To run it by hand instead:

```powershell
# Start MCP server in the foreground (runs continuously)
python -m oilwatch.mcp_server
```

The MCP server will be available for AI agents that support MCP protocol (such as Claude Desktop with MCP support).

---

## Price Normalisation (VAT)

Every scraped price is funnelled through `oilwatch/pricing.py` so the
"cheapest supplier" comparison is apples-to-apples:

- **`price_per_liter` is stored in GBP per litre, inclusive of VAT.**
- Domestic heating oil (kerosene) is subject to the **reduced 5% VAT rate**,
  which is applied uniformly to all suppliers.
- Pence values (`155.80p`) are converted to pounds (`1.558`) automatically.
- `total_price` is always `price_per_liter × quantity_liters`.

This fixes the previous bug where ValueOils was compared at 5% VAT while
HomeFuels Direct was compared at 20% VAT, producing a misleading ranking.

---

## How It Works

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         OilWatch                                │
├─────────────────────────────────────────────────────────────────┤
│  CLI              MCP Server           Scheduler                │
│  (Commands)       (AI Agent API)       (Automation)             │
└────────┬──────────────┬────────────────────┬────────────────────┘
         │              │                    │
         └──────────────┴────────────────────┘
                        │
         ┌──────────────▼──────────────┐
         │      Service Layer          │
         │   (OilWatchApp class)       │
         └──────────────┬──────────────┘
                        │
    ┌───────────────────┼───────────────────┐
    │                   │                   │
    ▼                   ▼                   ▼
┌─────────┐      ┌─────────────┐     ┌──────────┐
│Discovery│      │   Quotes    │     │Analytics │
│ Service │      │   Service   │     │ Service  │
└────┬────┘      └──────┬──────┘     └────┬─────┘
     │                  │                 │
     │         ┌────────▼────────┐        │
     │         │   Connectors    │        │
     │         ├─────────────────┤        │
     │         │ • Browser       │        │
     │         │ • HTTP/Scrape   │        │
     │         │ • Manual        │        │
     │         └────────┬────────┘        │
     │                  │                 │
     └──────────────────┼─────────────────┘
                        │
              ┌─────────▼─────────┐
              │   SQLite Database │
              │   (oilwatch.sqlite)│
              │                   │
              │ • suppliers       │
              │ • quotes          │
              │ • orders          │
              └───────────────────┘
```

### Data Flow

1. **Discovery** → Web search → Supplier registry
2. **Quote Collection** → Connectors → Price database
3. **Analytics** → Price history → Chart + cheapest supplier
4. **MCP/CLI** → User/AI requests → Service layer → Results

### Connector Types

| Type | How It Works | Suppliers |
|------|--------------|-----------|
| **HTTP scraping** | One request; price parsed from the response (HTML or XML) | ValueOils, HomeFuels Direct, Fueltool, Highland Fuels |
| **Browser automation** | Playwright drives the supplier's own quote form, sometimes behind a login session | Rix, Regency Oils, Connon Bros, Johnson Oils, Scottish Fuels, BoilerJuice |
| **Enquiry form + email** | Form submitted once; the reply price is read from the inbox and the message deleted | Gleaner Oils, Oilfast, Compass, Nationwide, Crown Oil |
| **Manual / phone** | Contact details plus a generated call sheet | Turriff Fuels, Carnegie Fuels, Brogan Fuels |

---

## Local Suppliers (Aberdeenshire Area)

Grouped by *how* each supplier is reached rather than by what it charges today —
run `oilwatch cheapest` for current figures. Hardcoded prices used to live here
and rotted within weeks.

### Automated (live prices collected)

| # | Supplier | Reached by | Website |
|---|----------|-----------|---------|
| 1 | ValueOils | HTTP scrape (`valueoils_auto`) | https://www.valueoils.com/Quote.aspx |
| 2 | HomeFuels Direct | HTTP scrape (`homefuels_live_price`) | https://homefuelsdirect.co.uk/home/heating-oil-prices/aberdeenshire |
| 3 | Fueltool — *UK-average benchmark, not a local supplier* | HTTP scrape (`fueltool`) | https://www.fueltool.co.uk/ |
| 4 | Rix | Browser (`rix_browser`) | https://www.rix.co.uk/locations/aberdeen-depot |
| 5 | Regency Oils | Browser (`fuelsoft`) | https://www.regencyoils.com/ |
| 6 | Connon Bros | Browser (`fuelsoft`) | https://connon.fuelsoft.co.uk/ |
| 7 | Johnson Oils | Browser (`fuelsoft`) | https://oilweb.johnstonfuels.co.uk/ |
| 8 | Highland Fuels | HTTP scrape (`highland_fuels`, IQO XML) | https://www.highlandfuels.co.uk/home-heating |
| 9 | Scottish Fuels | Browser (`scottish_fuels_browser`) plus email replies | https://quote.scottishfuels.co.uk/quote/ |

Scottish Fuels needs a live login session: `/quote/` answers 302 to its account
page once the session lapses, and the connector reports that rather than failing
obscurely. Re-establish it with `oilwatch login scottish_fuels`.

BoilerJuice has a browser connector that is written but not yet collecting a
price. Supplier accounts, where required, live in
`config/supplier_credentials.json` (gitignored).

### Quote by enquiry form, then email

These have no scrapeable price page. `oilwatch submit-requests` fills the form;
`oilwatch monitor-email` then records the reply price and deletes the message.
Each also has a phone number available through `oilwatch phone-script`.

| Supplier | Form platform |
|----------|---------------|
| Gleaner Oils | wpforms |
| Oilfast Insch | enquiry form |
| Compass Fuels | EasyOil |
| Nationwide Fuels | enquiry form |
| Crown Oil | enquiry form |

### Phone only

| Supplier | Phone | Email |
|----------|-------|-------|
| Turriff Fuels | 01888 562706 | — |
| Carnegie Fuels | 01356 648 648 | info@carnegiefuels.co.uk |
| Brogan Fuels | 0345 300 8844 | domestic@brogans.co.uk |

Carnegie's online ordering is suspended by its own notice, which points
customers at phone/email. Brogan Fuels trades as part of Scottish Fuels, so the
Scottish Fuels figure covers it.

---

## Configuration

### Main Config (`config/settings.json`)

```json
{
  "home": {
    "label": "Hatton of Fintray, Aberdeenshire, Scotland",
    "latitude": 57.23875,
    "longitude": -2.2643
  },
  "radius_miles": 50,
  "quote_quantity_liters": 1000,
  "default_postcode": "AB21 0YA",
  "scheduler": {
    "discovery_interval_hours": 168,
    "quote_interval_hours": 24
  }
}
```

### Credentials (`config/supplier_credentials.json`)

Supplier account passwords live here because the browser connectors must
supply the real password to sign in.

**This file is gitignored, and it is encrypted at rest** with Windows DPAPI
(`oilwatch/secretstore.py`), so it is readable only by your Windows account on
this machine — a copy taken to another machine or account is useless. Legacy
plain-JSON files are still read, and are re-encrypted on the next save. The
envelope looks like this (`blob` is base64-encoded binary ciphertext):

```json
{
  "format": "dpapi",
  "hint": "Encrypted with Windows DPAPI: readable only by this Windows account on this machine.",
  "blob": "<base64 ciphertext>"
}
```

---

## Shortcomings & Limitations

### Current Limitations

| Issue | Impact | Workaround |
|-------|--------|------------|
| **Session expiry** | A browser connector stops working once a supplier session lapses (Scottish Fuels 302s to its account page) | Re-run `oilwatch login <supplier>`; the connector now reports this instead of crashing |
| **Selector drift** | A supplier redesign silently breaks a scraper | Connectors fall back to `manual_action_required` and report what they saw; update the connector |
| **CAPTCHA on registration** | Accounts can't be fully auto-created | One-off manual sign-in |
| **Phone-only suppliers** | Oilfast, Turriff and Carnegie cannot be quoted automatically | `oilwatch phone-script` |
| **Price freshness** | Stored prices age | `oilwatch quote-all`, or the daily scheduler; `cheapest` ignores quotes older than `max_quote_age_days` (code default 30; this install sets 1, because a quote stands at most a day) and lists them as `excluded_suppliers` |

### Technical Debt

1. **Browser automation** is slow (10-30 s per supplier) and runs sequentially
2. **No rate limiting** on API discovery

### Known Issues

- Scottish Fuels needs a live login session; re-run `oilwatch login scottish_fuels` when it expires
- Chart only shows days on which quotes were actually collected
- Geocoding sometimes fails for suppliers with incomplete addresses
- Manually-imported spreadsheet prices share the `quotes` table with live scrapes, so recency filtering matters (see `max_quote_age_days`)

---

## File Structure

```
Oil Price Webscraper/
├── README.md                    # This file
├── ROADMAP.md                   # Development roadmap
├── PROGRESS.md                  # Current progress status
├── BROWSER_AUTOMATION.md        # Browser automation guide
├── SUPPLIER_CONNECTORS.md       # Supplier connectors and contacts
├── pyproject.toml               # Package configuration
├── config/
│   ├── settings.json            # Main configuration
│   └── supplier_credentials.json # All passwords
├── data/
│   ├── oilwatch.sqlite          # Database (generated)
│   └── oilwatch-market.png      # Latest chart (generated)
├── oilwatch/
│   ├── __init__.py
│   ├── cli.py                   # Command-line interface
│   ├── mcp_server.py            # MCP server for AI agents
│   ├── service.py               # Main application
│   ├── discovery.py             # Supplier discovery
│   ├── geo.py                   # Geocoding
│   ├── quotes.py                # Quote collection
│   ├── analytics.py             # Charts and analytics
│   ├── db.py                    # Database layer
│   ├── scheduler.py             # Job scheduling
│   ├── config.py                # Config loading
│   ├── credentials.py           # Password management
│   ├── auto_register.py         # Account registration
│   ├── api_discovery.py         # API endpoint discovery
│   └── connectors/
│       ├── base.py              # Base connector
│       ├── browser_base.py      # Browser automation base
│       ├── manual.py            # Manual connector
│       ├── price_page.py        # Price scraping
│       ├── http_form.py         # HTTP form submission
│       └── suppliers/           # Supplier-specific connectors
│           ├── valueoils.py
│           ├── homefuels_direct.py
│           ├── boilerjuice.py
│           ├── scottish_fuels_browser.py
│           └── ...
└── tests/
    └── test_app.py              # Unit tests
```

---

## Testing

Tests use Python's built-in `unittest` and run offline (HTTP is mocked, SQLite
uses a temp database), so no network or supplier sites are touched.

### Run from the command line

```powershell
# Activate the virtual environment
.venv\Scripts\activate

# Run the full suite
python -m unittest discover -s tests -t . -v

# Line + branch coverage of the package (needs the dev extra:
#   python -m pip install -e ".[dev]" )
python -m coverage run -m unittest discover -s tests -t .
python -m coverage report -m
# The report exits non-zero below the floor set in pyproject.toml
# ([tool.coverage.report] fail_under), so a silent slide fails the run.
```

### Run from VS Code

1. Open the project folder in VS Code.
2. Install the recommended extensions (`.vscode/extensions.json`) - the Python
   extension is required.
3. Open the **Testing** view (beaker icon in the sidebar) - tests are discovered
   automatically using the `unittest` config in `.vscode/settings.json`.
4. Run / debug individual tests, files, or the whole suite from there, or use
   the preconfigured **"OilWatch: run tests"** launch configuration.

Test files:

| File | Covers |
|------|--------|
| `tests/test_pricing.py` | VAT + pence/pounds normalisation |
| `tests/test_analytics.py` | cheapest, average, trend, recommendation |
| `tests/test_db.py` | SQLite supplier/quote/order storage |
| `tests/test_config.py` | settings + supplier-overrides loading |
| `tests/test_connectors.py` | manual / price-page / HTTP-form connectors |
| `tests/test_supplier_connectors.py` | ValueOils + HomeFuels Direct (mocked HTTP) |
| `tests/test_app.py` | end-to-end `OilWatchApp` wiring |

---

## Daily Usage Routine

### Morning Price Check (2 minutes)

```powershell
# 1. Check cheapest supplier
python -m oilwatch.cli cheapest

# 2. If the price is good, order by phone or on the supplier's own site,
#    then write down what you bought, from whom and for how much:
python -m oilwatch.cli record-purchase "HomeFuels Direct" --price-per-liter 1.0982
```

### Weekly Deep Dive (10 minutes)

```powershell
# 1. Collect all quotes
python -m oilwatch.cli quote-all --postcode "AB21 0YA"

# 2. Generate chart
python -m oilwatch.cli chart
start data\oilwatch-market.png

# 3. Call 2-3 manual suppliers with phone scripts
python -m oilwatch.cli phone-script --postcode "AB21 0YA"
```

---

## Support & Documentation

| Document | Purpose |
|----------|---------|
| `README.md` | This file - overview and quick start |
| `BROWSER_AUTOMATION.md` | Browser automation details |
| `SUPPLIER_CONNECTORS.md` | Supplier connectors and contact details |
| `ROADMAP.md` | Future development plans |
| `PROGRESS.md` | Current implementation status |

---

## License

MIT License

---

**Last Updated:** 2026-09-10
**Version:** 0.1.0
**Location:** Hatton of Fintray, Aberdeenshire, Scotland (AB21 0YA)

> Current state detail is in `PROGRESS.md`; near-term plans in `ROADMAP.md`.
> This is a personal tool for one home in Aberdeenshire, not a general-purpose
> product — the home location, the supplier set and the phone-only fallbacks are
> all deliberately specific to that use.
