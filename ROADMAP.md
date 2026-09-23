# Roadmap

## Phase 1: Clean rebuild

- Archive the original prototype.
- Create a maintainable Python package.
- Add local configuration, SQLite persistence, and a CLI.

**Status:** ✅ Complete

---

## Phase 2: Supplier registry

- Geocode Hatton of Fintray.
- Search for domestic heating oil suppliers in Aberdeenshire and nearby areas.
- Filter out directories and comparison sites.
- Geocode candidates and keep suppliers within roughly 50 miles.
- Persist and refresh the supplier registry over time.

**Status:** ✅ Complete

**Implementation:**
- `discovery.py` - DuckDuckGo HTML search, result parsing, contact enrichment
- `geo.py` - Geocoding via Nominatim, distance calculations via geopy
- `db.py` - SQLite schema with suppliers table, upsert logic, inactive marking

---

## Phase 3: Quote collection

- Add pluggable connectors for supplier quote workflows.
- Implement generic connectors for manual contact, direct price pages, and normal HTTP forms.
- Store quote history for 1,000 litre orders.

**Status:** ✅ Complete

**Implementation:**
- `connectors/base.py` - Abstract connector interface
- `connectors/manual.py` - Manual contact connector (phone/email/website)
- `connectors/price_page.py` - Web scraping connector for public price pages
- `connectors/http_form.py` - HTTP form submission connector with configurable fields
- `quotes.py` - Quote service orchestration
- `db.py` - Quotes table with supplier foreign key, historical tracking

---

## Phase 4: Market intelligence

- Compute latest cheapest supplier from the most recent successful quotes.
- Generate a time series chart for cheapest, average, and variance.

**Status:** ✅ Complete

**Implementation:**
- `analytics.py` - Market snapshot computation, matplotlib chart generation
- `db.py` - `latest_quotes()` query joining suppliers with most recent quote per supplier
- Chart shows: cheapest price, average price, variance over time (dual-axis)

---

## Phase 5: Ordering — dropped

- Add supplier-specific order placement hooks using the same connector architecture.
- Record attempted and successful orders in the database.

**Status:** ❌ Dropped 2026-09-11, by decision.

**Why:** the owner buys by phone or on the supplier's own site. The ordering
platform that was built for this — `ordering.py`, `OilWatchApp.place_order`, the
`place-order` command, `OrderResult` and a `place_order` on every connector —
never had a supplier configured to accept an automated order, so every call
returned a manual placeholder. It was machinery for a workflow that does not
exist, and it implied the tool could order.

**What replaced it:** `record-purchase`, which writes down a purchase already
made — from whom, for how much, with what code — and `purchases` to read it back.
The orders table is unchanged and still the record of what was bought.

---

## Phase 6: MCP Integration

- Expose registry refresh, quote collection and analytics tools through FastMCP.

**Status:** ✅ Complete

**Implementation:**
- `mcp_server.py` - 10 MCP tools exposed
- `scheduler.py` - APScheduler background jobs for recurring discovery and quote collection
- `cli.py` - Full CLI parity with MCP tools

---

## Current State Summary

*Verified 2026-09-10 — see `PROGRESS.md` for detail.*

| Component | Status | Notes |
|-----------|--------|-------|
| Package structure | ✅ Complete | Hatchling build, editable install |
| Configuration | ✅ Complete | JSON config: `config/settings.json` (personal values, gitignored) + `suppliers.json` (the tracked supplier register) |
| Database | ✅ Complete | SQLite; supplier registry and quote history (live counts via `oilwatch status`) |
| Supplier discovery | ✅ Complete | DuckDuckGo search, geocoding, 50-mile filter |
| Connectors | ✅ Complete | 4 generic + 14 supplier-specific (HTTP, Playwright browser; the phone route was removed 2026-09-22) |
| Quote collection | ✅ Complete | Pluggable; live browser + HTTP collection working |
| Analytics | ✅ Complete | Recency window (`max_quote_age_days`, default 30d) keeps 2007–2025 spreadsheet rows out of the current comparison |
| Ordering | ❌ Dropped | Buying is manual; `record-purchase` records what was bought |
| MCP server | ✅ Complete | 10 tools over streamable HTTP or stdio; OpenClaw spawns it on demand |
| Scheduler | ✅ Complete | Background jobs for discovery and quotes |
| CLI | ✅ Complete | 21 commands |
| Tests | ✅ Complete | 746 tests, all offline |
| Email intake | ✅ Complete | Microsoft Graph monitor: extract reply price, record, delete mail |
| Market context | ✅ Complete | Brent crude daily series from the EIA |

---

## Recommended Next Steps

### Short-term (Medium Priority)

None outstanding. The two that sat here — deepening test coverage, and error
handling and logging — are done; what they asked for is listed under "Completed
Since the Phases Above" with the evidence.

### Long-term (Low Priority)

1. **Delivery area validation** — waiting on evidence rather than on code.

   Nothing in this install has ever recorded a delivery refusal. Across 640 quote
   rows every supplier either priced the order or came back `no_quote_page`,
   `quote_by_request` or `no_price_found`, and no note anywhere says it does not
   cover the postcode. So the vocabulary change this needs —
   `outside_delivery_area`, so that a refusal stops reading as a failed parse —
   is deliberately **not** made yet: a value in a closed contract set that nothing
   produces is worse than no value, the same reason `reason` carries `None` for
   "unclassified" rather than a synonym for one of the real values.

   Nothing is lost while it waits. A browser refusal lands in `no_price_found`
   with the connector's own note already beside it, and an emailed one is logged
   as "nothing to record" and left re-scannable in Deleted Items rather than
   marked processed. Wire the reason against the first real refusal's text, which
   is the working rule for these scrapers.

   A model judgement was considered as the producer and rejected: `reason` is a
   field consumers branch on, and this repository uses `quote_judge` to *name* a
   sender in a warning, never to decide what a row means.

2. ❌ **Dropped 2026-09-23 — historical analysis** (seasonal trends, and price
   prediction from historical patterns).

   **Why:** the prediction half would put a number in front of someone deciding
   what to pay for oil, built from one household's sparse quotes; the seasonal
   half would be a second trend view beside the one `analytics.py` already gives,
   and the 406 priced rows that are old enough to carry a season are the imported
   2007–2025 spreadsheet history, whose provenance is not the same instrument as
   today's connectors. Both were written before the recency window and the
   spreadsheet import existed.

3. ❌ **Dropped 2026-09-23 — deployment hardening** (health check endpoints,
   always-on host).

   **Why:** it contradicts a decision this repository already made. OilWatch
   refreshes on request and nothing is autostarted (2026-09-15: the scheduler's
   Startup entry was retired, and the MCP server's before it), and OpenClaw spawns
   the stdio server on demand — so there is no long-running service to health-check
   and no always-on host to document. What state there is to report, `status` and
   `refresh_status` already report.

---

## Completed Since the Phases Above

These sat under "Recommended Next Steps" while they were open; all are finished
and kept here only as a pointer. See `PROGRESS.md` for the detail and for the
older completion history.

- `cheapest` recency fix — `latest_quotes(max_age_days=…)` driven by the
  `max_quote_age_days` setting.
- Supplier connectors, discovery and baseline quotes.
- OpenClaw gateway confirmed connected to `oilwatch`, with tool annotations.
- Stale `data/*.md` reports removed.
- The OpenClaw `oilwatch` host-address chore — the HTTP URL was replaced by an
  on-demand stdio server, so there is no address to keep in step and the logon
  Startup entry was retired.
- **Deepening test coverage** (2026-09-23) — the gap the item claimed is closed:
  every connector type has integration tests (`test_connectors.py`,
  `test_browser_connectors.py`, `test_supplier_connectors.py`,
  `test_sync_browser_connectors.py`, `test_manual_connectors.py`,
  `test_connector_bases/leftovers/lifecycle.py`, `test_browser_base_flows.py`,
  `test_form_submit*.py`), discovery is covered with HTTP mocked
  (`test_discovery.py`, `test_discovery_flows.py`), and the recency window and
  the other analytics edges are pinned in `test_analytics.py` (`the window is
  carried in both branches`) and `test_quote_validity.py`.
- **Error handling and logging** (2026-09-23) — structured logging is
  `oilwatch/logging_setup.py` (level from `OILWATCH_LOG_LEVEL`, rotating file for
  the unattended launchers, and the per-message lines that make a sweep's summary
  auditable); transient HTTP failures are retried in `oilwatch/http.py`
  (`RETRY_STATUSES` {429, 500, 502, 503, 504}, jittered exponential backoff, plus
  the transport's own retries), pinned by `test_http.py`; and geocoding now
  degrades gracefully rather than silently — `GeoService` paces itself to
  Nominatim's one-request-a-second policy, waits out a throttle on the provider's
  own `Retry-After`, and logs a throttle as a throttle instead of letting a 429
  read as "no such place" (`GeocoderRateLimited` subclasses
  `GeocoderServiceError`, which is exactly what the old handler swallowed it as).
- **The cheapest-supplier alert** (2026-09-23) — a Windows toast when a refresh
  changes which supplier is cheapest, naming both prices. It fires only on a run
  someone started (nothing here runs on a timer) and only on a change of
  *supplier*, because the incumbent repricing a penny is the ordinary case: a
  sweep is ten to thirty seconds per supplier and its output is a wall of JSON,
  which is where the one line that matters is easy to miss.
  `OilWatchApp._announce_the_cheapest_changed`, pinned by
  `tests/test_service_flows.py::CheapestAlertTests`. **Not built, deliberately:**
  email and SMS alerts — an email would go from the owner's own mailbox, which
  every other route in this tool treats as a deliberate act, and an SMS needs a
  provider and a number nobody has given.

