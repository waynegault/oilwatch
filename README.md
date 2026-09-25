# OilWatch

**Domestic Heating Oil Price Tracker for Aberdeenshire, Scotland**

OilWatch automatically tracks heating oil prices from local suppliers, finds the cheapest option, and exposes everything through both a CLI and MCP server for AI agent integration.

---

## What This Codebase Does

OilWatch solves the problem of finding the best price for domestic heating oil (kerosene) in the Hatton of Fintray, Aberdeenshire area.

### Core Capabilities

| Feature | Description | Status |
|---------|-------------|--------|
| **Supplier Discovery** | Finds local suppliers via web search, filters by 50-mile radius | ✅ Complete |
| **Automated Quotes** | HTTP scraping + browser automation extract live prices | ✅ 10 suppliers reached automatically |
| **Price Comparison** | Compares all suppliers, identifies cheapest option | ✅ Complete |
| **Historical Tracking** | Stores quote history for trend analysis | ✅ Complete |
| **Market Charts** | Generates price movement charts over time | ✅ Complete |
| **MCP Server** | Exposes all functions as AI agent tools | ✅ Complete |

### Current Automation Status

Each supplier below was collected live on **2026-09-15**. Prices move daily, so
this records *how* each supplier is reached rather than what it charges — run
`oilwatch cheapest` for today's figures. The full list, including the suppliers
with no machine-readable price and the connector each domain resolves to, is in
`user-guide.md` §6 and §9.

| Supplier | Reached by | Notes |
|----------|-----------|-------|
| **ValueOils** | HTTP scrape (`valueoils_auto`) | Regional price table |
| **HomeFuels Direct** | HTTP scrape (`homefuels_live_price`) | Live price element |
| **Fueltool** | HTTP scrape (`fueltool`) | UK-average benchmark, not a local supplier |
| **Highland Fuels** | HTTP scrape (`highland_fuels`) | IQO XML quote API — needs the postcode |
| **Rix** | Browser (`rix_browser`) | Remix quote tool; its form requires a phone number |
| **Regency Oils** | Browser (`fuelsoft`) | Fuelsoft WEBPLUS |
| **Connon Bros** | Browser (`fuelsoft`) | Fuelsoft WebOrdering |
| **Johnson Oils** | Browser (`fuelsoft`) | Fuelsoft WebOrdering |
| **Scottish Fuels** | Browser (`scottish_fuels_browser`) + email replies | Session lasts ~15 min; re-signs in automatically |
| **BoilerJuice** | Browser (`boilerjuice_browser`) + email replies | Broker/aggregator; quotes by email too |
| **Oilfast Insch, Turriff, Carnegie, Compass, Nationwide, Crown, Gleaner** | Quote form / email | No machine-readable price; see each supplier's quote page in the register, or `oilwatch submit-requests --by-email` |

Brogan Fuels is part of Scottish Fuels, so the Scottish Fuels figure covers it
and Brogan is no longer a supplier of its own. Where a supplier replies to an
enquiry by email, `oilwatch monitor-email` records the price and removes the
message from the inbox.

---

## Verified counts

These are read back out of the code by `tests/test_docs.py`, so a number here
that goes stale fails the suite rather than sitting here misleading — which is
what an earlier 216-vs-88 test count and an 8-vs-9 tool count were doing.

| What | Count |
|------|-------|
| Tests | 809, all passing offline |
| MCP tools | 10 (streamable HTTP, or spawned as stdio on demand) |
| CLI commands | 22 |
| Modules under `oilwatch/` | 55 Python files |

---

## Quick Start

### Installation

```powershell
# From the repository root (the folder containing pyproject.toml)
cd <path-to-oilwatch>

# Create and activate the virtual environment (Python 3.12+)
python -m venv .venv
.venv\Scripts\activate

# Copy the settings template and fill in your own home, postcode and queries
copy config\settings.example.json config\settings.json

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
python -m oilwatch.cli quote-all --postcode "AB00 0AA"

# Find cheapest supplier
python -m oilwatch.cli cheapest

# Generate price chart
python -m oilwatch.cli chart
```

`AB00 0AA` in these examples is a placeholder: `quote-all` geocodes the postcode
you pass and searches from there, so substitute your own.

---

## CLI Commands

### Price Commands

```powershell
# Get quotes from ALL suppliers (automated + manual instructions)
python -m oilwatch.cli quote-all --postcode "AB00 0AA"

# Get quote from SINGLE supplier (by ID number)
python -m oilwatch.cli quote 2 --postcode "AB00 0AA"  # ValueOils
python -m oilwatch.cli quote 3 --postcode "AB00 0AA"  # HomeFuels Direct

# Show CHEAPEST supplier with average and variance
python -m oilwatch.cli cheapest

# Every priced supplier as fields: effective price after any discount code, the
# code itself, kind, order_channel and contact. The per-row read, where `cheapest`
# is the winner alone and `status` carries no priced rows at all.
python -m oilwatch.cli current-prices

# Show cheapest supplier + price direction + buy/hold recommendation
python -m oilwatch.cli status

# Generate PRICE CHART showing market trends
python -m oilwatch.cli chart

# Per-supplier price lines over time, with Brent crude on a second axis
python -m oilwatch.cli time-series

# Refresh the Brent crude daily series from the EIA (network, no browser)
python -m oilwatch.cli update-brent
```

### Supplier Commands

```powershell
# List ALL suppliers with contact details
python -m oilwatch.cli suppliers

# List suppliers including inactive ones
python -m oilwatch.cli suppliers --include-inactive

# Discover NEW suppliers via web search
python -m oilwatch.cli discover

# Check no supplier is recorded twice (same name or email on two rows)
python -m oilwatch.cli duplicates
```

The `duplicates` check reports a supplier the register holds as more than one
row. `upsert_supplier` keys on `website`, so a supplier whose site moves used to
be inserted again rather than updated, and the two rows split its quote history
between them — that is how a Turriff Fuels twin appeared on 2026-09-22. That
write is now refused: the upsert raises `SupplierIdentityConflict` when the name
is already recorded against a different website, and the check names the pairs
that predate the refusal. Choosing the survivor is a judgement about identity, so
neither path merges or deletes.

### Enquiry and Email Commands

For the suppliers that quote only after you ask. `submit-requests` drives their
enquiry forms — and, with `--by-email`, writes to the ones with no form at all:
the app asks by form or by email and never telephones a supplier, so a record
with neither is simply not asked. `monitor-email` then reads the reply, records
the price and deletes the message, so a processed reply cannot be counted twice.
The sweep is the one thing here that *is* scheduled — it runs hourly on
weekdays, because a supplier answers when it chooses.

```powershell
# Fill every enquiry form the register asks for (see config/suppliers.json)
python -m oilwatch.cli submit-requests

# One supplier only
python -m oilwatch.cli submit-requests --suppliers gleaner_oils

# Ask the suppliers that have no form, at the address the register carries
python -m oilwatch.cli submit-requests --by-email

# Read replies and record their prices (needs the one-time login below)
python -m oilwatch.cli monitor-email

# One-time OAuth2 device-code sign-in for the mailbox grant
python -m oilwatch.cli login-email

# Sign in to a supplier's own site by hand, re-establishing a browser session
python -m oilwatch.cli login scottish_fuels
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
# Run the scheduler by hand for a while (daily quotes, weekly discovery).
# Nothing starts it automatically: prices are refreshed on demand, so this is
# opt-in.
python -m oilwatch.cli schedule --postcode "AB00 0AA"

# Serve the MCP tools over streamable HTTP, for a client that dials a URL.
# OpenClaw does not need this - it spawns the server over stdio on demand - and
# nothing starts it automatically at logon either way.
python -m oilwatch.mcp_server
```

An HTTP server has to be *running* for its tools to exist, so it is started by
whoever needs it rather than at logon. On a machine whose client lives in WSL and
whose install is on Windows, `oil-mcp-up` (and `oil-mcp-down`) do that: the first
starts the server on the Windows host and schedules its own stop after a timeout
you pass in minutes, so nothing is left running when the work is done. Prefer it
when a spawned child's slow start is dangerous — a stdio child that misses its
init budget can take a whole harness down with a child-cleanup rejection, an HTTP
server cannot — and note that a URL transport fails *survivably* when the server
is down: the tools are absent until something starts it.

Both launchers are version-controlled here and installed by `install-wsl-helpers.sh`,
run from *inside* WSL:

```bash
cd "/mnt/c/Users/wayne/GitHub/Python/Projects/Oil Price Webscraper"
./install-wsl-helpers.sh          # oil-mcp-up and oil-mcp-down into ~/.local/bin
~/.local/bin/oil-mcp-up 30        # then, before the first oilwatch call
~/.local/bin/oil-mcp-down         # or let the timeout do it
```

The installer is the only supported route on purpose: these files must reach WSL as
LF-only, and a script written from the Windows side comes back with CRLF, whose shebang
then fails to exec with an error that reads like a missing file. It strips the CRs on
the way in, re-running it reports drift rather than silently reinstalling, and
`tests/test_wsl_helpers.py` refuses to let the committed bytes rot.

### Utility Commands

```powershell
# Initialize database and import the supplier register
python -m oilwatch.cli init

# Import historical prices from the spreadsheet OilWatch replaced
python -m oilwatch.cli import-spreadsheet --path "Oil Prices.xls"

# Discover APIs on supplier websites (for development)
python -m oilwatch.cli api-discover --url "https://www.valueoils.com"

# Register accounts on supplier websites
python -m oilwatch.cli register --postcode "AB00 0AA"
```

---

## How an AI Agent Should Use This

OilWatch exposes all functionality through an **MCP (Model Context Protocol) server**, enabling AI agents to autonomously track oil prices and find the best deals.

### MCP Tools Available

The server exposes ten tools, all read-mostly. There is **no** ordering tool:
`place_order` does not exist, and an agent must never claim an order was placed.
Recording a purchase is a deliberate CLI act by the owner
(`oilwatch record-purchase`); agents can only read them back via `purchases`.

| Tool | Description | Parameters |
|------|-------------|------------|
| `list_suppliers` | Suppliers on record | None |
| `current_prices` | Latest price per supplier (£/L inc. VAT), as an envelope that explains an empty market: `quotes` plus `as_of`, `window_days`, `excluded_suppliers` (each row carrying `reason: outside_window` — it has a price, just an older one than `window_days` allows), `never_quoted`, `not_refreshed_suppliers`, `no_quote_suppliers` (last ask gave no price) and `failed_suppliers` (last ask raised), and `refresh` (whether a sweep is running now). Every row — including the ones *not* in `quotes`, which are the ones a reader has to ask — carries `kind`, `order_channel`, a `contact` of `{phone, email, url}`, and `order_page`/`valid_until` when recorded | None |
| `cheapest` | Cheapest supplier + market average and variance, including how long that offer stands (`valid_until`), the winner's `kind` / `order_channel` / `contact` / `order_page`, the `window_days` compared, and any `excluded_suppliers` the age window dropped (each with `reason: outside_window`) | None |
| `purchases` | Purchases already recorded, newest first, with totals and discount codes | None |
| `status` | Snapshot + price trend + buy/hold recommendation, including the last purchase, the same `no_quote_suppliers` / `failed_suppliers` split, `awaiting_reply` (requests still owed a price, oldest first — a form or an email is answered later by a person, so writing the ask down is the only way to tell a supplier thinking from one never asked), and `refresh` (whether a sweep is running now) | None |
| `chart` | Market summary chart; returns a file path | None |
| `time_series_chart` | Per-supplier prices with Brent crude on a second axis; returns a path | None |
| `refresh_prices` | Scrape fresh quotes from all suppliers — **slow** (minutes, browser automation). Returns `{cached, cooldown_minutes, refreshed_at, results}`; within the 10-minute cooldown it returns `cached: true` and starts nothing, and if a sweep is **already running** it returns `in_progress: true` with `started_at`/`started_by`/`seconds_ago` and starts nothing either. `background: true` instead starts the sweep as its own **detached process** and returns `{job_id, state, started_at, total}` at once | `postcode: str`, `force: bool`, `background: bool` |
| `refresh_status` | A background sweep's progress and results: `state` (`running`/`finished`/`failed`), `done` of `total`, the `error` when it failed, the same `results` a foreground call returns, and `stale: true` when a job still says running but its worker is gone | `job_id: str` (defaults to the newest) |
| `update_brent` | Fetch the latest Brent crude daily series from the EIA | None |

Each tool carries MCP `ToolAnnotations`, so a client can distinguish a safe read
(`readOnlyHint`) from a slow, world-touching scrape (`openWorldHint`) and gate
approvals accordingly.

Every quote row carries a **`status`**, and it is a closed three-value gate — a
consumer branches on it, so an unrecognised value degrades badly:

| `status` | Means | Other fields |
|----------|-------|--------------|
| `ok` | a price was read | `price_per_liter` and `total_price` are set |
| `manual_action_required` | no price the app can read; the supplier is contactable instead | both prices are `null`; `reason` says which kind of gap it is |
| `error` | the attempt itself raised | both prices are `null`; `reason` is usually `site_error` |

So the nullability is part of the contract: **the price fields are `null` for
everything except `ok`**, and `reason` is `null` when unclassified rather than
meaning "no reason applies".

Three separate vocabularies share the word `status` in this codebase, and only
the first is a quote row's: quote rows (`ok` / `manual_action_required` /
`error`), supplier rows (`active` / `inactive` / `manual_review`), and the result
of a form submission or registration (`submitted`, `unconfirmed`, `sent`,
`no_address`, …). `unconfirmed` is a submission the page did not acknowledge:
only `submitted` is written to `quote_requests` and afterwards reported as
`awaiting_reply`.

Wherever a supplier row is not `ok`, it carries a machine-readable `reason`
beside the prose in `notes`, so a gap can be explained without parsing English:

| `reason` | Means |
|----------|-------|
| `no_quote_page` | no web quote exists at all — ask by email |
| `quote_by_request` | a quote page exists, but it answers a *person*: it takes your details and replies, so there is no price to read |
| `browser_required` | this path cannot price the supplier and browser automation can — a quote form that has to be driven, sometimes behind a sign-in |
| `no_price_found` | the page answered and carried no price: a parse that found nothing, as against `site_error`, where the attempt raised. Which of the two tells you whether to retry or to look at the connector |
| `login_not_confirmed` | an authenticated portal did not sign in |
| `captcha` | a bot check stopped the flow |
| `site_error` | the attempt raised — timeout, HTTP error, or a parse failure |

`null` means unclassified, not "no reason": most connectors do not attribute one
yet, and that is not a claim that none applies.

Every priced row also says what it is and how to act on it, so a rule an agent
must not get wrong is a field rather than prose it has to remember:

| Field | Values | Means |
|-------|--------|-------|
| `kind` | `supplier`, `benchmark` | A `benchmark` is a figure, not a company you can buy from — Fueltool. |
| `order_channel` | `web`, `email`, `benchmark`, `none` | `web` means a human-orderable `order_page` is recorded. `email` means no page, and an address to ask. `none` means neither is recorded — a phone number alone lands here, because the app never rings a supplier, and it means *unrecorded*, not "cannot be ordered from". |
| `contact` | `{phone, email, url}` | `url` is the one link to act on: the ordering page when there is one, otherwise the site, which may only be a marketing page. |

### AI Agent Workflow

```python
# Example: an agent checks prices.

# 1. Read the current market position and recommendation
snapshot = status()

# 2. Refresh only when the stored prices look old — this takes minutes
if snapshot["trend"]["direction"] == "insufficient_data":
    refresh_prices(postcode="AB00 0AA")
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

OpenClaw runs the server **on demand over stdio**: its `mcp.servers.oilwatch`
entry spawns `.venv\Scripts\python -m oilwatch.mcp_server --stdio` when it needs
the tools, so nothing has to be running beforehand and there is no host address
to keep in step. To serve the same tools over streamable HTTP instead — for a
client that dials a URL, or to use them by hand:

```powershell
# Streamable HTTP on 0.0.0.0:8000/mcp (MCP_HOST / MCP_PORT are respected)
python -m oilwatch.mcp_server

# or run the repo's launcher, which sets those two variables:
start_mcp_server.bat
```

A WSL client reaching that HTTP endpoint must use the Windows host address (the
NAT gateway, e.g. `172.28.144.1`), never `localhost`; the stdio path above
sidesteps that. Any MCP-capable client can use the server either way.

One trap when configuring a client: **the repo path contains a space**
(`Oil Price Webscraper`). A client that builds the stdio command by splitting it
on spaces sees `.../Projects/Oil` and fails with `spawn .../Projects/Oil ENOENT`;
give such a client a space-free wrapper rather than the raw path. The two
`/mnt/c/...` interop caveats that go with it are in `user-guide.md` §Traps.

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
| **Manual / emailed enquiry** | Contact details; asked by email at the address on record | Turriff Fuels, Carnegie Fuels |

---

## Local Suppliers (Aberdeenshire Area)

Grouped by *how* each supplier is reached rather than by what it charges today —
run `oilwatch cheapest` for current figures. Hardcoded prices used to live here
and rotted within weeks.

The supplier-by-supplier detail — the connector each domain resolves to, its
endpoint, and the contact details the enquiry flows use — is in
**`user-guide.md` §6 and §9**. In short:

- **HTTP scrape:** ValueOils, HomeFuels Direct, Fueltool, Highland Fuels.
- **Browser, driving the supplier's own form:** Rix, Regency Oils, Connon Bros,
  Johnson Oils, Scottish Fuels, BoilerJuice.
- **Enquiry form, then emailed reply:** Gleaner Oils (wpforms 1933), Oilfast Insch
  (Gravity Forms), Compass Fuels (the `compass_sector_lead` lead form),
  Nationwide Fuels and Crown Oil (both posting to an `eforms.` host behind
  Cloudflare Turnstile).
- **No quote page — asked by email:** Turriff Fuels (rory@turriff-fuels.co.uk;
  01888 562706, turrifffuels.com) and Carnegie Fuels (info@carnegiefuels.co.uk;
  01356 648 648, whose online ordering is suspended by its own notice).

Brogan Fuels was retired on 2026-09-18: it is part of Scottish Fuels, so it is no
longer listed, quoted or reported on separately. Its connector is gone; the
Scottish Fuels connector covers the price, and the register's `excluded_domains`
stops discovery re-adding the domain.

Supplier accounts, where required, live in `config/supplier_credentials.json`
(gitignored). Scottish Fuels needs a live session; see §7 of the user guide.

---

## Configuration

### Main Config (`config/settings.json`)

Copy `config/settings.example.json` and edit it. That file is the complete,
shipped template, and `tests/test_docs.py` keeps its keys in step with what
`oilwatch/config.py` actually reads — so it cannot drift from the loader the way
a copy kept here would. (A copy here did: it omitted `database_path` and
`chart_path`, which the loader reads by subscript, so a settings file built from
it failed at startup with a `KeyError` rather than taking a default.)

Everything has a default except those two. The settings worth understanding
rather than copying:

| Key | Meaning |
|-----|---------|
| `home` | The delivery point: `label` is geocoded, `latitude`/`longitude` skip that. It is also the address a supplier is asked to deliver to |
| `default_postcode` | The postcode used when `config/contact.json` has none |
| `radius_miles` | How far from `home` discovery keeps a candidate supplier |
| `search_queries` | The web searches discovery runs |
| `quote_quantity_liters` | The order size every quote is for |
| `quote_max_workers` | How many suppliers are quoted at once; `1` restores the strictly sequential run |
| `max_quote_age_days` | The freshness window. Code default 30; this install sets 1, because a quote stands at most a day |
| `fuel_mail_min_probability` | How likely an unknown sender's mail must read as a fuel quote before the sweep names that sender |
| `scheduler.email_monitor_start_hour` / `_end_hour` / `_interval_hours` | The inbox sweep's window (inclusive) and the step through it, as a weekday cron. A window that runs backwards or a step of zero is refused by name rather than failing inside `range()` |
| `login_urls` | Where `oilwatch login <supplier>` signs in, per supplier key |
| `microsoft_client_id` | The Entra app (client) id for the mailbox grant — public, not a secret |

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
| **Suppliers with no web quote** | Turriff Fuels and Carnegie Fuels have no quote form and no published price | Asked by email — `oilwatch submit-requests --by-email`. A supplier with no address either is left unasked; the app never telephones one |
| **Quote-by-request suppliers** | Compass Fuels, Crown Oil, Gleaner Oils, Nationwide Fuels and Oilfast Insch have a form, not a price — it takes your details and replies | `oilwatch submit-requests`, then `monitor-email` reads the reply |
| **Price freshness** | Stored prices age | Refresh on demand — `oilwatch quote-all`, `refresh_prices` over MCP, or an agent turn; nothing is scheduled to do it; `cheapest` ignores quotes older than `max_quote_age_days` (code default 30; this install sets 1, because a quote stands at most a day) and lists them as `excluded_suppliers` |

### Technical Debt

1. **Browser automation** is slow (10-30 s per supplier); `quote-all` runs suppliers concurrently, capped by `quote_max_workers` (default 4; `1` restores the strictly sequential behaviour)
2. **No rate limiting** on API discovery

### Known Issues

- Scottish Fuels needs a live login session; re-run `oilwatch login scottish_fuels` when it expires
- Chart only shows days on which quotes were actually collected
- Geocoding sometimes fails for suppliers with incomplete addresses
- Manually-imported spreadsheet prices share the `quotes` table with live scrapes, so recency filtering matters (see `max_quote_age_days`)

### Deliberately not built

Each of these is a decision rather than an omission, and the reason is the part
worth keeping:

- **Ordering** — OilWatch places no orders and has no tool that does. Buying is
  manual; `record-purchase` writes down a purchase already made.
- **Email and SMS alerts** — a refresh raises a Windows toast when the cheapest
  *supplier* changes, and stops there. An alert email would go from the owner's own
  mailbox, which every other route in this tool treats as a deliberate act, and an
  SMS needs a provider and a number nobody has given. A price drop by the supplier
  already cheapest raises nothing either: that is the ordinary case, and an alert
  for it would be noise.
- **Seasonal analysis and price prediction** — a predicted price, built from one
  household's sparse quotes, is a number in front of someone deciding what to pay
  for oil; and the rows old enough to carry a season are the imported 2007–2025
  spreadsheet rather than this instrument.
- **Deployment hardening** (health checks, an always-on host) — nothing here is
  autostarted: prices are refreshed on request, and OpenClaw spawns the MCP server
  over stdio on demand. There is no long-running service to health-check.
- **A `reason` for a supplier declining your postcode** — deliberately absent from
  the vocabulary. No refusal has ever been recorded: across all 640 quote rows
  every supplier either priced the order or came back `no_quote_page`,
  `quote_by_request` or `no_price_found`. A value in a closed contract set that
  nothing produces is worse than none, so it waits for the first real refusal's own
  text.
- **A delivery-area check per postcode** — the same waiting game. What the app asks
  today is each supplier's quote form for *this* postcode, which is the only
  coverage that changes what an order from here costs.

---

## File Structure

```
Oil Price Webscraper/
├── README.md                    # This file - overview, install, CLI/MCP reference
├── AGENTS.md                    # The rules an agent needs - consumer contract
├── user-guide.md                # Day-to-day use, supplier reference, sign-in, agent brief
├── pyproject.toml               # Package configuration
├── oil-mcp-up.sh                # WSL: start the MCP HTTP server on demand (see below)
├── oil-mcp-down.sh              # WSL: stop it now
├── install-wsl-helpers.sh       # WSL: install both into ~/.local/bin
├── docs/
│   ├── inspection.md            # Repeatable audit checklist for this repo
│   └── schema.sql               # The database's structure, generated (see below)
├── config/
│   ├── suppliers.json           # The supplier register: suppliers + excluded_domains
│   ├── suppliers.example.json   # Its shipped shape, for a fresh install
│   ├── settings.example.json    # Copy to settings.json (gitignored)
│   └── supplier_credentials.json # All passwords
├── data/
│   ├── oilwatch.sqlite          # Database (generated, gitignored)
│   ├── oilwatch-history.sqlite  # Its sanitised price history (committed, see below)
│   ├── oilwatch-market.png      # Latest chart (generated)
│   └── oilwatch.log             # Unattended runs, rotating (generated)
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
├── tools/
│   ├── build_explorer.py        # Builds the data explorer page (see below)
│   ├── build_snapshot.py        # Builds the committed price-history snapshot
│   ├── dump_schema.py           # Writes docs/schema.sql from the code
│   └── explorer_template.html   # Its template
└── tests/
    └── test_app.py              # Unit tests
```

---

## Testing

Tests use Python's built-in `unittest` and run offline (HTTP is mocked, SQLite
uses a temp database), so no network or supplier sites are touched.

`python -m unittest discover -s tests -t .` — 809 tests, all offline.

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
# Measuring the MCP server, which runs as its own process, needs the .pth hook
# described in docs/inspection.md 1.9.
```

### Check before you commit

Both checkers are declared in the `dev` extra and both have their scope pinned in
the repository rather than inherited from a default: Ruff's rule set in
`pyproject.toml`, Pyright's analysis mode in `pyrightconfig.json`.

```powershell
# Lint. A finding is fixed, or silenced with "# noqa: <rule> - <reason>".
python -m ruff check .

# Types. Two questions, two runs:
#   bare `pyright`  - is the package clean? A handful of third-party artifacts
#                     remain, listed in docs/inspection.md 8.9.
#   `pyright tests` - is the test tree clean? It is, and it must stay that way.
python -m pyright
python -m pyright tests
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
python -m oilwatch.cli quote-all --postcode "AB00 0AA"

# 2. Generate chart
python -m oilwatch.cli chart
start data\oilwatch-market.png

# 3. Ask the register's form-less suppliers by email
python -m oilwatch.cli submit-requests --by-email
```

---

## Data Explorer

`tools/build_explorer.py` renders everything the database knows — today's quotes,
the historical league table, variance per supplier, the recorded purchases, and
Brent rescaled to £ per litre — into a single self-contained page:

```powershell
python tools/build_explorer.py           # writes data/oilwatch-explorer.html
python tools/build_explorer.py --check   # print the payload summary, write nothing
start data\oilwatch-explorer.html
```

It reads the database read-only and writes two files: the standalone page above,
and the body-only fragment the Artifact publisher wants. Both are gitignored —
each embeds a snapshot of the database, so they are rebuilt rather than
committed. The one network call is the USD→GBP reference rate the page converts
Brent with, fetched at build time and named on the page with its date; when that
fetch fails the page shows Brent in dollars rather than inventing a rate.

## Price-history snapshot

The live database is **not** committed — this repository is public and
`data/oilwatch.sqlite` carries the owner's correspondence (the domains of every
sender the sweep could not place), his mailbox's message ids and his delivery
postcode. What is worth versioning is the price history, so
`tools/build_snapshot.py` writes that half to `data/oilwatch-history.sqlite`,
which *is* committed:

```powershell
python tools/build_snapshot.py           # writes data/oilwatch-history.sqlite
python tools/build_snapshot.py --check   # what would be written, and whether the export is behind
```

Rebuild it in the same commit as the register or database change that moved it:
the export is a copy taken by hand, so nothing else notices it lagging. `--check`
says so — it compares each table by row count *and* by a digest of its values,
because correcting a value changes no count (a supplier's phone did exactly that
on 2026-09-24) and a count comparison alone would have called the export current.
The one thing it does not compare is `suppliers.last_seen_at`: applying the
register moves that clock reading on every row, and a check that fired on every
`init` is one nobody would read.

The database's **structure** is versioned too, and separately: `docs/schema.sql`
is generated by `tools/dump_schema.py` from a fresh `init_schema`, so the
post-migration shape — including the columns that exist only because an older
database was altered in place — is readable and reviewable without a row of data.
It is generated for the same reason the export is checked: a hand-written schema
drifts from the code, and the suite fails if the two disagree.

```powershell
python tools/dump_schema.py           # writes docs/schema.sql
python tools/dump_schema.py --check   # in step with the code, or behind
```

It keeps `suppliers`, `quotes`, `brent_crude`, `discounts` and `quote_requests`,
and withholds the rest: tables that identify the owner or his mail are not copied
(`sender_judgements`, `processed_messages`, `orders`), values that identify him
are nulled on the rows that are kept (`discounts.code`,
`quote_requests.postcode`), and his own details — read from `oilwatch.identity`,
so the scrub cannot drift from what the connectors use — are replaced with
`<redacted-…>` wherever they survive inside a kept note. The build is written
into a fresh database rather than copied and pruned, because SQLite leaves a
deleted row in the file's free pages; it then checks its own output, scanning
every text column *and* the file's bytes for those details and for samples from
the tables it dropped, and deletes the file rather than leave a snapshot that
still carries one. `snapshot_meta` inside the file records what was withheld.

## Support & Documentation

| Document | Purpose |
|----------|---------|
| `README.md` | This file - overview, install, CLI and MCP reference |
| `AGENTS.md` | The rules an agent needs: the shortest form of the brief, for anything driving the CLI or the MCP tools |
| `user-guide.md` | Day-to-day use: prices, ordering, the supplier reference, accounts and sign-in, troubleshooting, and the brief to hand an AI agent |
| `docs/inspection.md` | Repeatable audit checklist for this repository |

---

## License

MIT License

---

**Last Updated:** 2026-09-23
**Version:** 0.1.0
**Location:** Hatton of Fintray, Aberdeenshire, Scotland

> This is a personal tool for one home in Aberdeenshire, not a general-purpose
> product — the home location and the supplier set are all deliberately specific
> to that use.
