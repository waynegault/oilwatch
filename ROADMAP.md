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

## Phase 5: Ordering

- Add supplier-specific order placement hooks using the same connector architecture.
- Record attempted and successful orders in the database.

**Status:** ✅ Complete (platform ready)

**Implementation:**
- `ordering.py` - Order service delegating to connectors
- `connectors/http_form.py` - Order placement with configurable fields and reference extraction
- `connectors/manual.py` - Manual order placeholder
- `db.py` - Orders table with status tracking, reference storage

**Note:** Platform is complete but requires supplier-specific configuration in `config/supplier_overrides.json` for automated ordering.

---

## Phase 6: MCP Integration

- Expose registry refresh, quote collection, analytics, and ordering tools through FastMCP.

**Status:** ✅ Complete

**Implementation:**
- `mcp_server.py` - 8 MCP tools exposed
- `scheduler.py` - APScheduler background jobs for recurring discovery and quote collection
- `cli.py` - Full CLI parity with MCP tools

---

## Current State Summary

*Verified 2026-09-10 — see `PROGRESS.md` for detail.*

| Component | Status | Notes |
|-----------|--------|-------|
| Package structure | ✅ Complete | Hatchling build, editable install |
| Configuration | ✅ Complete | JSON-based config in `config/settings.json` + `supplier_overrides.json` |
| Database | ✅ Complete | SQLite; currently 26 suppliers, 185 quotes, 0 orders |
| Supplier discovery | ✅ Complete | DuckDuckGo search, geocoding, 50-mile filter |
| Connectors | ✅ Complete | 4 generic + 16 supplier-specific (HTTP, Playwright browser, telephone) |
| Quote collection | ✅ Complete | Pluggable; live browser + HTTP collection working |
| Analytics | ✅ Complete | Recency window (`max_quote_age_days`, default 30d) keeps 2007–2025 spreadsheet rows out of the current comparison |
| Ordering | ✅ Platform only | Needs supplier-specific config |
| MCP server | ✅ Complete | 8 tools over streamable HTTP; registered in OpenClaw as `oilwatch` |
| Scheduler | ✅ Complete | Background jobs for discovery and quotes |
| CLI | ✅ Complete | 19 commands |
| Tests | ✅ Complete | 88 tests, all offline |
| Email intake | ✅ Complete | Microsoft Graph monitor: extract reply price, record, delete mail |
| Market context | ✅ Complete | Brent crude daily series from the EIA |

---

## Recommended Next Steps

### Immediate (High Priority)

1. **DONE — `cheapest` recency bug fixed**
   - `latest_quotes()` took the newest *priced* quote per supplier with no age
     limit, so 2007–2025 spreadsheet-import rows beat today's scraped prices: it
     reported **Gleaner Oils at 63.68p/L (2025-02-06)** when the cheapest real
     quote was **HomeFuels Direct at £1.0416/L (2026-09-09)**
   - Now `latest_quotes(max_age_days=…)`, driven by the new `max_quote_age_days`
     setting (default 30 days); `cheapest`, `current_prices` and `status` use it
   - Regression tests added in `tests/test_db.py` and `tests/test_app.py`

2. **Keep the OpenClaw `oilwatch` URL pointed at the right host address**
   - Now `http://172.28.144.1:8000/mcp` — the WSL **NAT gateway**. On 2026-09-10
     Wayne reverted WSL from mirrored to NAT, which invalidated the previous
     `192.168.33.56` LAN-IP URL
   - `localhost`/`127.0.0.1` never work from WSL and the Tailscale address times
     out. The correct address changes with the WSL networking mode, and the NAT
     gateway changes if the WSL vEthernet is recreated — so re-read
     `wsl -e ip route` (`default via …`) after a mode change or reboot

3. **DONE — supplier connectors, discovery, baseline quotes**
   - 26 suppliers registered, 185 quotes stored (17 active suppliers)
   - 16 supplier-specific connectors under `oilwatch/connectors/suppliers/`
   - Working: ValueOils, HomeFuels Direct, Fueltool (HTTP); Rix, Regency Oils,
     Connon Bros/Fuelsoft, Scottish Fuels (browser); phone scripts elsewhere

4. **DONE — gateway confirmed connected to `oilwatch`**
   - `openclaw mcp probe oilwatch` (OpenClaw 2026.9.x) reports **8 tools,
     resources, prompts**
   - Tool annotations were added on 2026-09-10 (`readOnlyHint` /
     `destructiveHint` / `idempotentHint` / `openWorldHint`, plus titles), so
     OpenClaw no longer prompts for approval on every OilWatch call

5. **DONE — stale `data/*.md` reports removed**
   - `data/SUPPLIER_PRICES.md`, `data/AUTOMATION_STATUS.md` and
     `data/REGISTRATION_SUMMARY.md` were dated 2026-03-23 and contradicted each
     other on price and VAT; nothing generated or read them, so they were
     deleted (recoverable from git history)

### Short-term (Medium Priority)

6. **Deepen test coverage** — 88 tests exist and pass, but gaps remain
   - Add integration tests for each connector type
   - Add tests for discovery service (mock HTTP)
   - Add tests for analytics edge cases, especially the recency window above

7. **Enhance error handling and logging**
   - Add structured logging throughout
   - Add retry logic for transient HTTP failures
   - Add graceful degradation for geocoding rate limits

### Long-term (Low Priority)

8. **Delivery area validation**
   - Add postcode-level delivery area checks per supplier
   - Some suppliers may not deliver to all postcodes within 50 miles

9. **Notification system**
   - Add email/SMS alerts when a new cheapest supplier is found
   - Add price drop alerts for tracked suppliers

10. **Historical analysis**
    - Add seasonal trend analysis
    - Add price prediction based on historical patterns

11. **Deployment hardening**
    - Run the MCP server as a scheduled task/logon service rather than a
      foreground `start_mcp_server.bat`
    - Add health check endpoints
    - Document deployment to always-on host (VM, Raspberry Pi, etc.)

