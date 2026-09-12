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
- `mcp_server.py` - 9 MCP tools exposed
- `scheduler.py` - APScheduler background jobs for recurring discovery and quote collection
- `cli.py` - Full CLI parity with MCP tools

---

## Current State Summary

*Verified 2026-09-10 — see `PROGRESS.md` for detail.*

| Component | Status | Notes |
|-----------|--------|-------|
| Package structure | ✅ Complete | Hatchling build, editable install |
| Configuration | ✅ Complete | JSON-based config in `config/settings.json` + `supplier_overrides.json` |
| Database | ✅ Complete | SQLite; supplier registry and quote history (live counts via `oilwatch status`) |
| Supplier discovery | ✅ Complete | DuckDuckGo search, geocoding, 50-mile filter |
| Connectors | ✅ Complete | 4 generic + 16 supplier-specific (HTTP, Playwright browser, telephone) |
| Quote collection | ✅ Complete | Pluggable; live browser + HTTP collection working |
| Analytics | ✅ Complete | Recency window (`max_quote_age_days`, default 30d) keeps 2007–2025 spreadsheet rows out of the current comparison |
| Ordering | ❌ Dropped | Buying is manual; `record-purchase` records what was bought |
| MCP server | ✅ Complete | 9 tools over streamable HTTP or stdio; OpenClaw spawns it on demand |
| Scheduler | ✅ Complete | Background jobs for discovery and quotes |
| CLI | ✅ Complete | 21 commands |
| Tests | ✅ Complete | 556 tests, all offline |
| Email intake | ✅ Complete | Microsoft Graph monitor: extract reply price, record, delete mail |
| Market context | ✅ Complete | Brent crude daily series from the EIA |

---

## Recommended Next Steps

### Short-term (Medium Priority)

1. **Deepen test coverage** — the suite is green (see PROGRESS.md for the
   current count), but gaps remain
   - Add integration tests for each connector type
   - Add tests for discovery service (mock HTTP)
   - Add tests for analytics edge cases, especially the recency window
     described in PROGRESS.md

2. **Enhance error handling and logging**
   - Add structured logging throughout
   - Add retry logic for transient HTTP failures
   - Add graceful degradation for geocoding rate limits

### Long-term (Low Priority)

3. **Delivery area validation**
   - Add postcode-level delivery area checks per supplier
   - Some suppliers may not deliver to all postcodes within 50 miles

4. **Notification system**
   - Add email/SMS alerts when a new cheapest supplier is found
   - Add price drop alerts for tracked suppliers

5. **Historical analysis**
   - Add seasonal trend analysis
   - Add price prediction based on historical patterns

6. **Deployment hardening**
   - Add health check endpoints
   - Document deployment to always-on host (VM, Raspberry Pi, etc.)

---

## Completed Since the Phases Above

These sat under "Recommended Next Steps" while they were open; all are finished
as of 2026-09-12 and kept here only as a pointer. See `PROGRESS.md` for the
detail and for the older completion history.

- `cheapest` recency fix — `latest_quotes(max_age_days=…)` driven by the
  `max_quote_age_days` setting.
- Supplier connectors, discovery and baseline quotes.
- OpenClaw gateway confirmed connected to `oilwatch`, with tool annotations.
- Stale `data/*.md` reports removed.
- The OpenClaw `oilwatch` host-address chore — the HTTP URL was replaced by an
  on-demand stdio server, so there is no address to keep in step and the logon
  Startup entry was retired.

