# OilWatch User Guide

How to use OilWatch: getting today's prices, ordering, recording what you bought,
the phone fallback, accounts and sign-in, and the supplier-by-supplier reference.

For what the project is, how to install it and the full CLI/MCP surface, see
`README.md`. For current implementation status and plans, see `PROGRESS.md` and
`ROADMAP.md`.

---

## 1. Getting today's prices

**A quote stands for about a day.** `max_quote_age_days` is set to `1` in
`config/settings.json` (the code default, 30, exists only to keep historical
spreadsheet rows out of the comparison). Anything a day old is a dead offer.

**Nothing refreshes prices on a schedule.** There is no background job keeping
them current — that is deliberate. When prices look stale, fetching them is an
explicit act:

```powershell
# Every supplier (browser automation; roughly 1-3 minutes, one visible window)
python -m oilwatch.cli quote-all --browser --postcode "AB21 0YA"

# One supplier, by id (run `oilwatch suppliers` for the ids)
python -m oilwatch.cli quote 1 --postcode "AB21 0YA" --browser

# Read the market
python -m oilwatch.cli cheapest    # winner + average + variance
python -m oilwatch.cli status      # snapshot + trend + buy/hold read
python -m oilwatch.cli chart       # market chart PNG
python -m oilwatch.cli time-series # per-supplier lines + Brent
```

The same three reads are MCP tools; only `refresh_prices` scrapes.

### Reading the output

- **`excluded_suppliers`** — quotes outside the freshness window. A thin market
  is "not re-quoted yet", not a broken scrape.
- **`not_refreshed_suppliers`** — suppliers still inside the window but whose
  latest *attempt* returned no price, so the figure is from an earlier run.
  Quote the date with the price and this cannot mislead.
- **`benchmark`** — Fueltool's UK average. It is held out of the average, the
  variance and the ranking deliberately: it is context, not an offer.
- **`effective_price_per_liter`** — the posted price less any usable discount
  code. The stored price is already inclusive of 5% VAT, so this is the
  like-for-like figure.

---

## 2. Ordering

OilWatch does not place orders and there is no tool that does. You buy by phone
or on the supplier's own site.

**Always quote the supplier's ordering URL next to the price**, and name the
discount code when one applies — a price with no way to act on it is half an
answer. `cheapest` and `current_prices` carry `website` and `discount` for
exactly this.

Discount codes are per-supplier rows in the `discounts` table; a code wider than
the order (a minimum litres threshold) is not applied.

---

## 3. Recording a purchase

Recording is separate from buying: nothing here drives a browser or contacts a
supplier. Write down what happened, so the database knows what you actually paid
as well as what was on offer.

```powershell
# By name, name fragment, or id (an ambiguous name is refused, not guessed)
python -m oilwatch.cli record-purchase "Scottish Fuels" --price-per-liter 1.0894 --code autumn25

# If you know the total rather than the unit price
python -m oilwatch.cli record-purchase "Scottish Fuels" --total 1089.40 --litres 1000

python -m oilwatch.cli purchases   # newest first
```

`--litres`, `--code`, `--reference`, `--date` and `--notes` are optional.
Prices are GBP per litre inclusive of VAT, like every other price stored.

---

## 4. Phone quotes

`phone-script` builds a call sheet for the suppliers that only quote by phone or
email:

```powershell
python -m oilwatch.cli phone-script `
  --quantity-liters 1000 `
  --postcode "AB21 0YA" `
  --name "Your Name" `
  --address "Your Address" `
  --output data/quote-calls.json
```

It prints a quick-reference card with the phone numbers, a per-supplier script
and a recording checklist; `--output` writes the run to JSON.

---

## 5. Quote requests by email

Some suppliers quote only from an enquiry form. `submit-requests` fills those
forms; `monitor-email` then reads the reply, records the price and **deletes the
message** so a processed reply cannot be counted twice.

```powershell
python -m oilwatch.cli submit-requests --suppliers gleaner_oils,oilfast
python -m oilwatch.cli monitor-email
```

Supplier replies are one-off quotes: check the inbox after requesting, and once
the price is recorded let the monitor delete it. A reply from a supplier not yet
in `SUPPLIER_DOMAINS` (or in a format `extract_ppl` cannot parse) needs the map or
the parser extended rather than left unprocessed. So the sweep says what it did —
one line per recorded price and a closing `sweep: N new message(s), M recorded,
K from unrecognised sender(s)` — and names the domains it could not place. That
line is how an oil company mailing from an unlisted domain (a CRM, a marketing
host, a second brand) is noticed at all: its mail is skipped, so without it the
sweep looks identical to one that found nothing. Subjects are not logged.

The email sweep is a *separate* mechanism from price refresh and **is**
scheduled — a supplier answers when it chooses, so polling is the only way to
notice the reply without watching the inbox.

---

## 6. How each supplier is reached

| Supplier | Reached by | Notes |
|----------|-----------|-------|
| **ValueOils** | HTTP scrape (`valueoils_auto`) | Regional price table |
| **HomeFuels Direct** | HTTP scrape (`homefuels_live_price`) | Live price element |
| **Fueltool** | HTTP scrape (`fueltool`) | UK-average **benchmark**, not a supplier you can order from |
| **Highland Fuels** | HTTP scrape (`highland_fuels`) | IQO XML quote API — needs the postcode |
| **Rix** | Browser (`rix_browser`) | Remix quote tool; phone number is required by its form |
| **Regency Oils** | Browser (`fuelsoft`) | Fuelsoft WEBPLUS |
| **Connon Bros** | Browser (`fuelsoft`) | Fuelsoft WebOrdering |
| **Johnson Oils** | Browser (`fuelsoft`) | Fuelsoft WebOrdering |
| **Scottish Fuels** | Browser (`scottish_fuels_browser`) plus emailed replies | Session lasts ~15 min; re-signs in automatically |
| **BoilerJuice** | Browser (`boilerjuice_browser`) | Broker; also quotes by email |
| **Oilfast, Turriff, Carnegie, Compass, Nationwide, Crown, Gleaner** | Enquiry form and/or phone | No machine-readable price — see `phone-script` and each supplier's `order_page` |

Brogan Fuels is part of Scottish Fuels, so the Scottish Fuels figure covers it;
Brogan was retired as a supplier of its own on 2026-09-18. Last verified live
**2026-09-15**; prices move daily, so run `oilwatch cheapest` for today's numbers
rather than trusting any figure written in a document.

---

## 7. Accounts, credentials and sign-in

### Where the account email comes from

`config/contact.json` (gitignored) or `OILWATCH_EMAIL` — never from source.

### The credential store

`config/supplier_credentials.json` holds supplier account passwords, because the
browser connectors must supply the real one to sign in. It is gitignored and
**encrypted at rest with Windows DPAPI** (`oilwatch/secretstore.py`), so it is
readable only by this Windows account on this machine; a copy taken elsewhere is
useless. Legacy plain-JSON files are still read and are re-encrypted on the next
save.

Generated passwords look like `ScottishFuels!aB3xK9mQ` — `[SupplierName]!` plus
eight random characters.

### Reading a password back

Only when you are registering or signing in by hand:

```powershell
.venv\Scripts\python -c "from oilwatch.credentials import get_supplier_credentials as g; print(g('scottish_fuels')['password'])"
```

**A password is never written into a connector's note, tool output or the log.**
Notes are returned to the caller, stored in the `quotes` table and logged, so a
password in one is broadcast three ways at once. The registration note names the
store and the command above instead.

### Registering an account

1. Trigger the connector so credentials exist:
   ```powershell
   python -m oilwatch.cli quote 1 --postcode "AB21 0YA"
   ```
2. Read the generated password back from the store (above).
3. Register on the supplier's site with your email and that password.
4. Later quote attempts sign in automatically.

`oilwatch register` drives the registration forms it knows; a CAPTCHA there means
a one-off manual sign-in rather than a full auto-create.

### Sessions

- **Scottish Fuels** is the one supplier whose quote needs a session, and its
  cookie lasts about **15 minutes**, so a mid-run re-sign-in is routine rather
  than exceptional. The connector signs in again with the stored credentials
  automatically; a successful one logs `signed in`.
- Its sign-in is the flaky part: the site's own submit handler often swallows the
  Sign In click, so nothing is posted. The connector requires the page to
  actually move on, falls back to Enter and then a JavaScript click, and reports
  what the page showed when it still fails. `/quote/` will also serve a
  signed-out visitor, so a lapsed session does not necessarily cost you the
  quote.
- Re-establish a session by hand at any time: `oilwatch login scottish_fuels`.

### Where the persistent browser lives

The Selenium/undetected-chromedriver stack keeps a per-supplier Chrome profile:

```
C:\Users\<you>\.oilwatch\browser_profiles\<name>\     (e.g. scottish_fuels, form_submit)
C:\Users\<you>\.oilwatch\browser_profiles\<name>\cookies.json
```

The patched driver is `%APPDATA%\undetected_chromedriver\undetected_chromedriver.exe`
and it drives your installed Chrome. The profile is what carries the sign-in
between runs; nothing loads the JSON cookie backup back in.

---

## 8. Browser automation internals

### Three stacks, not one

| Stack | Modules | Used for |
|-------|---------|----------|
| **Playwright (async)** | `connectors/browser_base.py` and its subclasses (BoilerJuice, ValueOils, HomeFuels Direct) | Quote extraction from JavaScript-rendered pages, plus API request interception |
| **Playwright (sync)** | `connectors/suppliers/fuelsoft.py`, `rix_browser.py` | Fill-and-read quote flows; kept synchronous because their callers are |
| **Selenium + undetected-chromedriver** | `browser_auth.py`, `form_submit.py`, `scottish_fuels_browser.py` | Interactive login (persistent Chrome profile) and enquiry-form submission, where anti-bot measures matter |

Playwright is the default. The Selenium stack exists only where a persistent,
less-detectable session is required, and it runs **headful** on purpose —
reCAPTCHA scores a headless browser too low. Merging it into Playwright is
deliberately deferred: its whole value is the persistent profile, and the flows
are live-verified and cannot be exercised offline.

The two synchronous connectors share `connectors/sync_browser.py` (launch and
teardown plus the ok/manual result shaping).

### Two traps that produced wrong answers

- **Never clear a pre-filled form control before typing.** On Scottish Fuels the
  quantity box arrives pre-filled, and the page's own validation rewrites an
  emptied one to its 500 L minimum — so clearing it turned every 1000 L quote
  into a 500 L one that was still reported as 1000 L. Set a value only when it
  differs, through the DOM with the events the page listens for.
- **Report the site's own figures**, not arithmetic on top of them. Where a
  supplier states a total, use it; if the site quotes a different quantity than
  the one asked for, say so rather than relabelling it.

---

## 9. Supplier connector reference

### How selection works

Two registries in `oilwatch/connectors/suppliers/__init__.py` drive everything:

- `_CONNECTORS` — public class name → the submodule defining it, held as strings
  so a class is imported only when something asks for it.
- `_SUPPLIER_CONNECTORS` — supplier domain fragment → `(http, browser)`, either
  side `None` where that kind does not exist.

`get_supplier_connector(website, prefer_browser=False)` matches the domain and
returns a connector:

- By default only the **HTTP** connector is used — one request against a
  server-rendered page.
- **Browser** connectors are opt-in with `prefer_browser=True` (the CLI's
  `--browser`).
- `valueoils.com` and `homefuelsdirect.co.uk` are in `_HTTP_WINS_OVER_BROWSER`:
  their browser connectors are unreliable while the HTTP one works, so they never
  take the browser path.
- When a supplier is browser-only and `prefer_browser` was not requested, the
  function returns `None` and the caller falls back to a manual quote.

Resolution is lazy (PEP 562), so an HTTP-only run never imports Playwright.

### Domain → connector

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
| `boilerjuice.com` | — | `BoilerJuiceBrowserConnector` |
| `fuelsoft.co.uk` | — | `FuelsoftConnector` |
| `johnstonfuels.co.uk` | — | `FuelsoftConnector` |

`FuelsoftConnector` covers every Fuelsoft-hosted supplier from one
implementation (Connon Bros, Johnson Oils, Regency Oils' WebOrdering).

### HTTP endpoints

| Connector | Endpoint |
|-----------|----------|
| `ValueOilsConnector` | `https://www.valueoils.com/Quote.aspx` (regional `/regions/scotland/aberdeenshire/`) |
| `HomeFuelsDirectConnector` | `https://homefuelsdirect.co.uk/home/heating-oil-prices` (Aberdeenshire page) |
| `FueltoolConnector` | `https://www.fueltool.co.uk/` |
| `HighlandFuelsConnector` | `https://iqo-highland.fuels.app/lib/getoffers.php` — IQO XML quote API |
| `OilfastConnector` | `https://oilfast.co.uk/depot/insch/` (enquiry form; no scrapable price) |
| `RixConnector` | `https://www.rix.co.uk/fuels/heating-oil` |
| `RegencyOilsConnector` | `https://www.regencyoils.com` |
| `ScottishFuelsConnector` | `https://scottishfuels.co.uk/heating-oil-in-aberdeenshire/` |

### Browser entry points

| Connector | Entry point |
|-----------|-------------|
| `RixBrowserConnector` | `https://fuelquote.rix.co.uk/` |
| `FuelsoftConnector` | Fuelsoft WebOrdering forms (Connon Bros, Johnson Oils, Regency Oils) |
| `ScottishFuelsBrowserConnector` | `https://quote.scottishfuels.co.uk/quote/` (login `/customer/account/login/`) |
| `ValueOilsBrowserConnector` | `https://www.valueoils.com/regions/scotland/aberdeenshire/` |
| `HomeFuelsDirectBrowserConnector` | `https://homefuelsdirect.co.uk/home/heating-oil-prices/aberdeenshire` |
| `BoilerJuiceBrowserConnector` | `https://www.boilerjuice.com/uk/journeys/core/quote` |

### Generic and manual connectors

A supplier with no scrapable quote resolves to contact details plus instructions,
or to one of three generic types (`GENERIC_CONNECTOR_TYPES` in
`oilwatch/connectors/__init__.py`):

| `connector_type` | Connector | Use |
|------------------|-----------|-----|
| `manual` | `ManualConnector` | Contact details only; returns `manual_action_required` |
| `price_page` | `PricePageConnector` | A price read from a plain page |
| `http_form` | `HTTPFormConnector` | One form POST |

Domain matching is tried first; a supplier row may name a `connector_type`
explicitly, and an unknown type is a configuration error rather than a silent
downgrade to a manual quote.

### Contact details

Used by the manual connectors, the enquiry-form path and `phone-script`.

| Supplier | Contact |
|----------|---------|
| Oilfast (Insch) | 01464 635999 · 03302 320 104 · insch@oilfast.co.uk · https://oilfast.co.uk/depot/insch/ |
| Rix | Aberdeen 01224 455477 · general 0800 542 4207 · montsales@rix.co.uk |
| Regency Oils | 0800 838500 · https://www.regencyoils.com |
| Scottish Fuels | 0345 300 8844 · Aberdeen 01224 213 132 · info@scottishfuels.co.uk |
| Gleaner Oils | 01224 877575 · https://www.gleaner.co.uk/get-a-quote-or-place-an-order/ |
| Compass Fuels | 0330 128 9838 · https://compassfuels.co.uk/ |
| Crown Oil | 0330 123 1444 · https://www.crownoil.co.uk/#quote-form-wrapper |
| Nationwide Fuels | 0330 678 0880 · https://www.nationwidefuels.co.uk/ |
| Turriff Fuels | 01888 562706 · https://www.turrifffuels.com/ |
| Carnegie Fuels | 01356 648 648 · info@carnegiefuels.co.uk (online ordering suspended) |

### Adding a connector

1. Add the module under `oilwatch/connectors/suppliers/`.
2. Register the class in `_CONNECTORS` (public name → submodule string).
3. Add the domain row to `_SUPPLIER_CONNECTORS` as `(http_name, browser_name)`,
   `None` for a kind that does not exist.
4. Cover it and try it:

   ```powershell
   python -m unittest tests.test_connector_imports
   python -m oilwatch.cli quote <supplier_id>
   ```

### File structure

```
oilwatch/connectors/
├── base.py                   # BaseConnector
├── browser_base.py           # BrowserConnector (Playwright, async)
├── sync_browser.py           # SyncBrowserConnector / sync_page
├── manual.py                 # ManualConnector
├── price_page.py             # PricePageConnector
├── http_form.py              # HTTPFormConnector
└── suppliers/
    ├── __init__.py           # _CONNECTORS + _SUPPLIER_CONNECTORS + selection
    ├── valueoils.py / valueoils_browser.py
    ├── homefuels_direct.py / homefuels_direct_browser.py
    ├── fueltool.py
    ├── highland_fuels.py
    ├── oilfast.py
    ├── rix.py / rix_browser.py
    ├── regency_oils.py
    ├── scottish_fuels.py / scottish_fuels_browser.py
    ├── boilerjuice.py
    ├── fuelsoft.py
    └── telephone.py          # TelephoneQuoteScript
```

---

## 10. API discovery

For development: find the endpoints a supplier's site actually uses.

```powershell
python -m oilwatch.cli api-discover --url "https://scottishfuels.co.uk/quote/" --output "sf_api.json"
python -m oilwatch.cli api-discover --supplier-id 1 --output "supplier1_api.json"
```

The output lists endpoints, request/response data, JSON payloads and any auth
tokens seen. Look for `/api/`, `/quote`, `/price`, `/cart` and JSON responses
carrying price data; a discovered endpoint can become a direct connector.

Worked example — ValueOils: discovery found only chat-widget endpoints
(`va.tawk.to/v1/widget-settings`, `/v1/session/start`). Its prices are
server-rendered and the quote form posts traditionally, so there is no API to
target — which is why it is scraped rather than called.

---

## 11. Troubleshooting

**Make a failure say what it saw, then fix against the real text.** Every
connector here logs the page text or response body when a parse fails; read that
before changing a selector or a regex. Guessing at markup is how a wrong answer
gets shipped.

### Browser automation fails

Symptom: a quote returns an error mentioning the browser.

1. Install the Playwright browsers: `python -m playwright install chromium`.
   (Playwright's browser cache is shared and version-pinned; upgrading Playwright
   in one environment *deletes* the build other environments still use, which
   shows up as `Executable doesn't exist`.)
2. Run the one supplier with a visible browser to watch it.
3. Check for a CAPTCHA or anti-bot challenge. If a challenge is genuinely in
   play, the answer is a real sign-in in the persistent profile (already the
   design for the Selenium stack), not a workaround against the challenge.

### Sign-in fails

Symptom: `sign-in did not take`, or the login form is still on screen.

1. Check the credentials exist — `oilwatch register` writes them, and the store
   is DPAPI-encrypted so it cannot be read as plain text.
2. Try it by hand: `oilwatch login <supplier>`.
3. **Scottish Fuels specifically:** the credentials are usually not the problem.
   The site's own submit handler swallows the click, so *no POST is sent* and the
   page just sits there; a failed attempt logs the landed URL, whether the login
   form is still present, the reCAPTCHA field *lengths* and the page text. Read
   that before changing anything. Expect `manual_action_required` for it
   sometimes — that is site intermittency, not an OilWatch fault.

### A price looks wrong

- Re-read the connector's note: it names the quantity and the basis the supplier
  stated. A figure that does not match the site's own total is a bug — report it,
  do not re-derive it by hand.
- Check the connector did not disturb a pre-filled control (see §8).
- `oilwatch quote <id> --browser` re-quotes one supplier so you can compare.

### Nothing is fresh

That is usually correct, not broken — see §1. Reading a day-old price without
saying so is the one way to get this wrong.

---

## 12. Security notes

- Credentials are encrypted at rest with DPAPI, readable only by this Windows
  account on this machine; the file is gitignored.
- A registration note never contains the password: notes are returned to the
  caller, written into the `quotes` table and logged, so a password in one would
  be broadcast three ways.
- Page loads are bounded (`PAGE_LOAD_TIMEOUT_S`), so a stalled third-party script
  fails the step rather than blocking a run indefinitely.
- Chrome's own password manager is disabled for the automated profile: it
  refills the login fields mid-run and appends a second copy of the value.

---

## 13. Traps

- **Never pass a `/mnt/c/...` path as an *argument*** to the Windows Python from
  WSL: interop translates the executable path but not argument paths. `-m` needs
  none.
- **A space in the repo path breaks any client that builds the stdio command by
  splitting it on spaces.** This repo lives under `Oil Price Webscraper`, so such
  a client sees `.../Projects/Oil` and fails with `spawn .../Projects/Oil ENOENT`.
  Point it at a space-free wrapper, not the raw path.
- **Do not rely on the process working directory.** Config, the credential store
  and the database all resolve from the checkout (`config.CHECKOUT_ROOT`), found
  from the package's own location — a spawned process does not inherit the repo
  as its cwd.

---

## 14. Handing this to an AI agent (OpenClaw / Hal)

Paste the block below into an agent that has this machine's MCP server
configured. It is self-contained: the agent does not need the repository checked
out or the code read to use OilWatch correctly.

---

You have **OilWatch** — a heating-oil price tracker for Wayne's delivery address
(Hatton of Fintray, AB21 0YA, Aberdeenshire). It is a Python project at
`/mnt/c/Users/wayne/GitHub/Python/Projects/Oil Price Webscraper`, reachable from
WSL through the Windows venv python at `.venv/Scripts/python.exe`.

There are two doors onto the same engine. Use them like this.

## Reads — the `oilwatch` MCP tools (all instant, no browser)

- **`current_prices`** — the latest quote per supplier (£/L inc. VAT), as an
  envelope: `quotes`, plus `as_of` and `window_days` and the five ways a supplier
  can be missing from it — `excluded_suppliers` (older than the window),
  `never_quoted` (never priced at all), `not_refreshed_suppliers` (priced
  earlier, latest attempt failed), `no_quote_suppliers` (the last ask gave no
  price) and `failed_suppliers` (the last ask raised). An empty `quotes`
  therefore says *why* it is empty instead of being a dead end.
- **`cheapest`** — the winner, plus the market average and variance.
- **`status`** — the snapshot, the trend, a buy/hold read, and the same
  `no_quote_suppliers` / `failed_suppliers` split, so a sweep can be read back in
  one call.
- **`list_suppliers`** — the supplier list.
- **`purchases`** — the recorded purchase history (read-only; see Writing).
- **`chart`** / **`time_series_chart`** — write a PNG and return its path.
- **`update_brent`** — refresh the Brent crude series (network, no browser).

## The one that costs minutes — call it deliberately

**`refresh_prices`** scrapes every supplier with browser automation. It takes
roughly **1–3 minutes** (the MCP request timeout is 300 s), drives real browsers
(one of them a visible window), and **fails per-supplier** — a CAPTCHA, a site
change, a shut depot. Partial results are normal and are not an error.

- Do **not** call it in a loop.
- Do **not** call it just to look at a price.
- Call it when the quotes are stale — see below.
- If the call does hit the 300 s timeout, do **not** assume the scrape failed:
  read `current_prices` and look at the `observed_at` dates to see what actually
  landed. Optionally pass a `postcode`; otherwise the configured delivery address
  is used.
- **A repeat call within 10 minutes will not start a second sweep.** If one ran
  that recently, the call returns `{"cached": true, "results": []}` with
  `refreshed_at` and `minutes_ago` naming the sweep it reused — read
  `current_prices` for the prices on record. This exists because the usual reason
  to call twice is a client timeout mid-sweep (`mcporter`'s default per-call
  budget is 60 s against a 1–3 minute sweep), and a second call would launch every
  browser again. Pass `force=true` when a sweep really is wanted.

## Freshness: quotes are good for about a day

A heating-oil quote stands for **at most ~1 day** — that is a ceiling, not an
average. So "what are prices today?" means *refresh, then read*. If a read comes
back thin, or carries an `excluded_suppliers` list, that means the market has not
been re-quoted today: it is a **stale snapshot, not a scrape failure**.

**Nothing refreshes these on a schedule** — there is no background job keeping
prices current (that is deliberate: prices are fetched on request). So if the
dates are old, the refresh is yours to make, and reporting a day-old price
without saying so is the one way to get this wrong.

## How to report a price to Wayne

- **Always give the supplier's ordering URL (its `website`) next to the price**,
  and name the discount code when one applies. A price with no way to act on it
  is half an answer.
- Give the **`observed_at` date** with the price, so a day-old quote is never
  read as today's.
- Prices are £ per litre **inclusive of 5% VAT**, for the configured quantity
  (1000 L). Say which basis you are quoting.
- **Fueltool is a UK-average benchmark, not a supplier you can order from** —
  say so rather than presenting it as the winner.
- Several suppliers are **phone/email-only**. They come back
  `manual_action_required` with contact details. That is expected, not a failure.
- **Scottish Fuels often undercuts the field, but its automatic sign-in is
  intermittent** — expect `manual_action_required` for it sometimes. That is not
  an OilWatch fault and not an MCP fault: do not try to "fix" it, and do not
  report it as breakage. Say the quote is missing, and that its own site is worth
  checking by hand when the ranking matters.

## Writing — through the CLI, not MCP

Recording a purchase is a **deliberate act by Wayne**; it is never something you
infer from a price:

```bash
cd "/mnt/c/Users/wayne/GitHub/Python/Projects/Oil Price Webscraper"
PYTHONUNBUFFERED=1 .venv/Scripts/python.exe -m oilwatch.cli record-purchase \
  "Scottish Fuels" --price-per-liter 1.0894
```

The supplier may be a name fragment or an id (an ambiguous name is refused rather
than guessed); `--total`, `--litres`, `--code`, `--reference`, `--date` and
`--notes` are optional. The MCP `purchases` tool only reads them back.

Anything you want to **watch while it runs** is better from the CLI, where you
see progress and a real exit code:

```bash
cd "/mnt/c/Users/wayne/GitHub/Python/Projects/Oil Price Webscraper"
PYTHONUNBUFFERED=1 .venv/Scripts/python.exe -m oilwatch.cli quote-all --browser
# OILWATCH_LOG_LEVEL=DEBUG for per-supplier progress
```

`oilwatch.cli --help` lists all commands.

## Traps

- **Never pass a `/mnt/c/...` path as an *argument*** to the Windows python: WSL
  interop translates the executable path but not argument paths. `-m` needs none.
- **A client that builds the stdio command by splitting it on spaces cannot use
  this path.** The repo lives under `Oil Price Webscraper`, so a client that
  word-splits the command sees `.../Projects/Oil` and fails with
  `spawn .../Projects/Oil ENOENT`. Give such a client a space-free wrapper (a
  launcher that execs this venv's `python -m oilwatch.mcp_server --stdio`)
  rather than the raw path.

  Verified against mcporter on 2026-09-18. This fails —

  ```
  mcporter call --stdio "/mnt/c/Users/wayne/GitHub/Python/Projects/Oil Price Webscraper/.venv/Scripts/python.exe" "current_prices()"
  # Error: spawn /mnt/c/Users/wayne/GitHub/Python/Projects/Oil ENOENT
  ```

  — while a space-free wrapper (here `/home/wayne/.local/bin/oilwatch-mcp`,
  containing `#!/bin/sh` and an `exec` of the venv's python with
  `-m oilwatch.mcp_server --stdio`) succeeds. Two further traps in that client:
  the tool argument must be **function syntax**, `"current_prices()"` — a dotted
  selector like `oilwatch.current_prices` returns `Unknown tool` — and its
  default per-call timeout is **60 s**, so `refresh_prices` (1-3 minutes) times
  out unless `--timeout` or `MCPORTER_CALL_TIMEOUT` is raised.
- The MCP server is **spawned per session over stdio** — there is no port to
  check and no server to start. If the tools look unhealthy,
  `openclaw mcp probe oilwatch` should report **9 tools**.
- Don't edit this repository to work around a supplier problem. Report what the
  tool said and let Wayne decide.
