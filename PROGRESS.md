# OilWatch Progress Summary

**Date:** 11 September 2026
**Package version:** 0.1.0 (unchanged since the prototype — see `pyproject.toml`)

> This file supersedes a stale March-2026 version that described an empty
> database and a single test file. None of that is true any more. Figures below
> were verified on 2026-09-11.

---

## Executive summary

OilWatch tracks domestic heating-oil prices around Hatton of Fintray,
Aberdeenshire (AB21 0YA) to answer one question: **which supplier is cheapest
right now, and is it a good moment to buy?**

It is a working system, not a prototype:

| Area | State |
|------|-------|
| Modules under `oilwatch/` | 53 Python files |
| Supplier connectors | 16 supplier-specific, plus 4 generic |
| CLI commands | 21 |
| MCP tools | 9 (served over streamable HTTP) |
| Tests | 547, all passing offline |
| Database | 26 suppliers, 244 quotes, 0 orders |

---

## Verified components

### Core

| File | Purpose |
|------|---------|
| `cli.py` | CLI entry point (21 commands) |
| `cli_handlers.py` | One handler per CLI command; the browser/Graph ones live here |
| `service.py` | `OilWatchApp` — orchestration used by both CLI and MCP |
| `mcp_server.py` | FastMCP server, 9 tools, streamable HTTP on `/mcp` |
| `scheduler.py` | APScheduler jobs for recurring discovery / quotes |
| `db.py` | SQLite layer (`data/oilwatch.sqlite`) |
| `models.py`, `config.py`, `pricing.py` | Data models, settings, VAT + £/p normalisation |
| `credentials.py` | Supplier credential loading |

### Collection

| File | Purpose |
|------|---------|
| `discovery.py` | DuckDuckGo supplier discovery |
| `geo.py` | Nominatim geocoding + distance |
| `api_discovery.py` | Reverse-engineering of supplier quote APIs |
| `auto_register.py` | Supplier account registration attempts |
| `browser_auth.py` | Login + persisted session handling |
| `form_submit.py` | Quote-request form submission |
| `quotes.py` | Quote collection orchestration |
| `graph_email.py` | Poll the inbox via Microsoft Graph, extract replies and discount codes, delete processed mail |
| `email_parsing.py` | Supplier reply domains + the price parser the Graph monitor reuses |
| `import_xls.py` | Import `Oil Prices.xls` history |
| `brent.py` | Brent crude daily series from the EIA |
| `analytics.py` | Cheapest / average / variance, trend, charts |

### Connectors

Generic: `base.py`, `manual.py`, `price_page.py`, `http_form.py`,
`browser_base.py`.

Supplier-specific (`oilwatch/connectors/suppliers/`, 16): `valueoils`,
`valueoils_browser`, `homefuels_direct`, `homefuels_direct_browser`, `rix`,
`rix_browser`, `scottish_fuels`, `scottish_fuels_browser`, `regency_oils`,
`fuelsoft`, `fueltool`, `boilerjuice`, `highland_fuels`, `oilfast`,
`brogan_fuels`, `telephone`.

Collection methods split three ways: plain HTTP where the price is
server-rendered, browser automation (Playwright) where a form or login gates it,
and a telephone script generator for the phone-only depots.

### CLI commands

`init`, `discover`, `suppliers`, `quote`, `quote-all`, `cheapest`, `status`,
`chart`, `time-series`, `import-spreadsheet`, `update-brent`,
`record-purchase`, `purchases`, `schedule`, `phone-script`, `api-discover`,
`register`, `login`, `submit-requests`, `monitor-email`, `login-email`.

There is no `place-order`: OilWatch does not order. Buying happens by phone or on
the supplier's own site, and `record-purchase` writes down what was bought, from
whom and for how much.

### MCP tools

`list_suppliers`, `current_prices`, `cheapest`, `purchases`, `status`, `chart`,
`time_series_chart`, `refresh_prices`, `update_brent`.

`refresh_prices` runs browser automation and takes minutes; OpenClaw is
configured with a 300 s request timeout to accommodate it.

### Tests

`python -m unittest discover -s tests -t .` — 547 tests, all offline (mocked HTTP,
temp SQLite).

Covers pricing/VAT, analytics, DB, config, connectors, supplier connectors,
Fueltool/Fuelsoft parsing, browser auth, Brent, spreadsheet import, email
monitoring, and end-to-end app wiring.

---

## Database (as of 2026-09-11)

- **Path:** `data/oilwatch.sqlite`
- **Suppliers:** 26 (17 `active`, the rest historical)
- **Quotes:** 244
- **Orders:** 0

**Added 2026-09-10 — purchases can be recorded.** The `orders` table was
write-only: `place_order` wrote to it but nothing ever read it back, so "who did
I buy from last time, and what did I pay" had no answer, and no agent could see
it. `oilwatch record-purchase` now writes down a purchase the owner made
themselves (supplier by name fragment or id, price per litre *or* total paid,
optional discount code and reference), with an ambiguous name refused rather
than guessed. `oilwatch purchases` and the read-only MCP `purchases` tool read
them back, and `status` carries the last one. Nothing in this path drives a
browser or contacts a supplier — recording is kept separate from buying.

Quote timestamps span **2007-01-26 → 2026-09-11**, because
`import-spreadsheet` loaded the historical workbook. Recent automated runs
(2026-09-09 22:19–22:40 and 2026-09-10 00:13) produced priced `ok` quotes for
Scottish Fuels, Rix, Regency Oils, Connon Bros, Johnson Oils, HomeFuels Direct,
Highland Fuels, Fueltool and ValueOils.

**Fixed 2026-09-10 — `cheapest` used to ignore recency.** `latest_quotes()` took
the most recent *successful priced* quote per supplier with no age limit, so
suppliers whose only priced row came from the spreadsheet import won on
19-month-old numbers: `cheapest` reported **Gleaner Oils at 63.68p/L
(2025-02-06)** when the cheapest real quote was **HomeFuels Direct at £1.0416/L
(2026-09-09)**, and the average/variance were polluted the same way.
`latest_quotes(max_age_days=…)` now takes a cut-off, driven by the
`max_quote_age_days` setting (default 30 days); `cheapest`, `current_prices` and
`status` all use it. Regression tests in `tests/test_db.py` and
`tests/test_app.py`.

---

## Automation status

- **Working end-to-end:** ValueOils, HomeFuels Direct, Fueltool (HTTP);
  Rix, Regency Oils, Connon Bros / Johnston, Fuelsoft platform (browser);
  Scottish Fuels (browser, Magento + reCAPTCHA login).
- **Phone-only:** Oilfast Insch, Turriff Fuels, Brogan Fuels, Carnegie Fuels,
  Compass Fuels, Gleaner Oils, Highland Fuels.
- **Scheduled:** daily email monitor (`monitor_email.bat`), plus the in-process
  scheduler for discovery and quotes.

### MCP / OpenClaw integration

The server is registered in OpenClaw (`~/.openclaw/openclaw.json`) as
`mcp.servers.oilwatch`, `transport: "streamable-http"`, URL
`http://172.28.144.1:8000/mcp` — the WSL **NAT gateway**, i.e. the Windows host.

WSL cannot reach a Windows-hosted service on `localhost`/`127.0.0.1`, and the
address that works **depends on the WSL networking mode**: under NAT (current) it
is the NAT gateway `172.28.144.1`; under mirrored it was the LAN IP
`192.168.33.56`. Switching modes silently breaks the integration — after any
change verify from WSL and re-read `wsl -e ip route` (the `default via` line).

Verified 2026-09-10 with OpenClaw 2026.9.2: `openclaw mcp probe oilwatch`
reports **9 tools, resources, prompts**, and an MCP `initialize` handshake from
WSL returns `serverInfo: {"name":"oilwatch","version":"0.1.0"}`. That version
used to read `3.1.1` — the MCP framework's own, because the server declared no
version of its own; it now reports the package's.

It is started at logon by a per-user Startup entry (see next action 4), which
runs `start_mcp_server.bat` (binds `0.0.0.0:8000`).

---

## Documentation debt

- **Resolved 2026-09-10:** the stale `data/SUPPLIER_PRICES.md`,
  `data/AUTOMATION_STATUS.md` and `data/REGISTRATION_SUMMARY.md` reports — dated
  2026-03-23, mutually contradictory on price and VAT, and carrying
  account/credential notes — were deleted. Nothing generated or read them, and
  they remain in git history if ever needed.
- `README.md`'s body was refreshed against verified state on 2026-09-10 (its
  automation table had claimed "2 of 8 suppliers" and a hand-set HomeFuels price,
  both long obsolete). It now records *how* each supplier is reached rather than
  hardcoding prices that rot.
- **Resolved 2026-09-10:** the owner's name and email were hardcoded as defaults
  across ~10 modules, so they were published with the repository. Identity now
  comes from `config/contact.json` (gitignored) or the `OILWATCH_*` environment
  variables via `oilwatch/identity.py`, and `tests/test_identity.py` fails the
  build if a personal literal reappears. Two postcodes were also in conflict in
  source (`AB52 6TA` vs `AB21 0YA`); both now resolve to
  `settings.default_postcode`. The old values remain in git history.
- The README's MCP section listed seven tools that do not exist
  (`init_database`, `collect_all_quotes`, `place_order`, …) with a workflow that
  would have failed. Replaced with the tools the server actually serves, and a
  note that there is no ordering tool.
- **Resolved 2026-09-11:** the location literals that the identity pass above
  left behind are gone: `mcp_server.py` no longer repeats the home postcode in
  `refresh_prices`, and `cli.py` and `auto_register.py` no longer repeat the
  address as `--address`/parameter defaults. Those now resolve through
  `oilwatch.identity` or `settings.home.label`. The MCP server's instructions no
  longer embed the location either — they needed config at import to do so — so
  importing `mcp_server` now reads no settings at all.
  `tests/test_docs.py` derives the tool, command and test counts stated in this
  file from the code, so the numbers above fail the suite when they go stale
  rather than sitting here misleading — which is what the 216-vs-88 test count
  and the 8-vs-9 tool count were doing.

---

## Next actions

1. **DONE — the database is the source of truth** (decided 2026-09-10). The
   workbook is retained only as a historical import: the hardcoded `P:\` path is
   gone and `import-spreadsheet` now requires an explicit `--path`.
2. **Keep the OpenClaw `oilwatch` URL valid** — it points at the WSL NAT gateway
   (`172.28.144.1`), which changes if the WSL vEthernet is recreated; re-check
   with `wsl -e ip route` after a mode change or reboot.
3. **DONE 2026-09-11 — `max_quote_age_days: 1` is deliberate, not a knob to
   loosen.** Heating-oil quotes stand for at most a day — 24h is the ceiling,
   not an average — which the code already assumes elsewhere (`models.py`
   defaults `valid_until` to `observed_at + 24h`, and the schema migration
   backfills the same), so the 1-day window here matches reality. The `30` in
   `oilwatch/config.py` is only the un-configured fallback (and keeps historical
   spreadsheet rows out of the comparison). Instead of widening the window,
   `cheapest` and `status` now list `excluded_suppliers` — the suppliers it held
   back and the last price each gave — so a thin snapshot reads as "not
   re-quoted yet" rather than looking like a scrape failure.
4. **DONE 2026-09-11 — the MCP server starts at logon.** It no longer has to be
   started by hand: a per-user Startup entry
   (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\OilWatch MCP
   Server.bat`) runs `start_mcp_server.bat` minimised at every logon, so the
   repo launcher stays the single place host/port live. It is a Startup entry
   rather than a Task Scheduler task because creating a task needs elevation —
   `schtasks /Create /SC ONLOGON` returned Access denied. For a true task
   (hidden, restart-on-failure, starts before logon) run this once from an
   elevated prompt, then delete the Startup entry:
   `schtasks /Create /TN "OilWatch MCP Server" /TR "\"C:\Users\wayne\GitHub\Python\Projects\Oil Price Webscraper\start_mcp_server.bat\"" /SC ONLOGON /F`
   The server takes ~15 s after logon before it answers, so an early probe
   getting "connection refused" is normal, not a fault.
5. **DONE 2026-09-11 — one virtualenv: `.venv`.** The working copy had
   accumulated a second environment (`.venv-1`) built from the current
   `pyproject.toml`, while `.venv` — the one every script and doc references —
   still held an older dependency set (fastmcp 3.1.1 against 3.4.7) and older
   editable-install metadata. `.venv` was rebuilt from the current
   `pyproject.toml` and the stray removed. Rebuilding surfaced a real gap: `xlrd`
   is imported at module level by `oilwatch/brent.py` and `oilwatch/import_xls.py`
   and by their tests, but was never declared, so a fresh `pip install -e .`
   yielded an environment that failed `update-brent` and those tests. It is now a
   declared dependency. Two environment steps are easy to miss on a rebuild: the
   dev extras (`pip install -e .[dev]`, for `coverage`) and Playwright's browser
   download, which is version-pinned — a playwright upgrade needs a fresh
   `playwright install chromium` and prunes the superseded build.
