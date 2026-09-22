# OilWatch Progress Summary

**Date:** 22 September 2026
**Package version:** 0.1.0 (unchanged since the prototype — see `pyproject.toml`)

> This file supersedes a stale March-2026 version that described an empty
> database and a single test file. None of that is true any more. Figures below
> were verified on 2026-09-16; the test count, the tool count and the database
> figures listed here were re-checked on 2026-09-22. Individual sections name the
> date they were checked where that is not this one; the database counts are as
> of the date stated beside them.

---

## Executive summary

OilWatch tracks domestic heating-oil prices around Hatton of Fintray,
Aberdeenshire (AB21 0YA) to answer one question: **which supplier is cheapest
right now, and is it a good moment to buy?**

It is a working system, not a prototype:

| Area | State |
|------|-------|
| Modules under `oilwatch/` | 54 Python files |
| Supplier connectors | 14 supplier-specific, plus 4 generic |
| CLI commands | 20 |
| MCP tools | 10 (streamable HTTP, or spawned as stdio on demand) |
| Tests | 709, all passing offline |
| Database | 17 active suppliers (28 including retired), 602 quote rows, 1 order (2026-09-18) |

---

## Verified components

### Core

| File | Purpose |
|------|---------|
| `cli.py` | CLI entry point (20 commands) |
| `cli_handlers.py` | One handler per CLI command; the browser/Graph ones live here |
| `service.py` | `OilWatchApp` — orchestration used by both CLI and MCP |
| `mcp_server.py` | FastMCP server, 10 tools; streamable HTTP on `/mcp`, or `--stdio` |
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
| `graph_email.py` | Poll the inbox via Microsoft Graph, extract replies and discount codes, delete processed mail, and log what each sweep did — including senders it could not place, since that mail is skipped |
| `quote_judge.py` | Ask TypeSafe's Jev whether mail from an unrecognised sender reads like a fuel quote; the verdict is cached per sender |
| `email_parsing.py` | Supplier reply domains + the price parser the Graph monitor reuses |
| `import_xls.py` | Import `Oil Prices.xls` history |
| `brent.py` | Brent crude daily series from the EIA |
| `analytics.py` | Cheapest / average / variance, trend, charts |

### Connectors

Generic: `base.py`, `manual.py`, `price_page.py`, `http_form.py`,
`browser_base.py`.

Supplier-specific (`oilwatch/connectors/suppliers/`, 14): `valueoils`,
`valueoils_browser`, `homefuels_direct`, `homefuels_direct_browser`, `rix`,
`rix_browser`, `scottish_fuels`, `scottish_fuels_browser`, `regency_oils`,
`fuelsoft`, `fueltool`, `boilerjuice`, `highland_fuels`, `oilfast`.

Collection methods split two ways: plain HTTP where the price is
server-rendered, and browser automation (Playwright) where a form or login gates
it. Suppliers with no machine-readable price are asked by their enquiry form or
by email; there is no phone connector, because the app never rings a supplier.

### CLI commands

`init`, `discover`, `suppliers`, `quote`, `quote-all`, `cheapest`, `status`,
`chart`, `time-series`, `import-spreadsheet`, `update-brent`,
`record-purchase`, `purchases`, `schedule`, `api-discover`,
`register`, `login`, `submit-requests`, `monitor-email`, `login-email`.

There is no `place-order`: OilWatch does not order. Buying happens by phone or on
the supplier's own site, and `record-purchase` writes down what was bought, from
whom and for how much.

### MCP tools

`list_suppliers`, `current_prices`, `cheapest`, `purchases`, `status`, `chart`,
`time_series_chart`, `refresh_prices`, `refresh_status`, `update_brent`.

`refresh_prices` runs browser automation and takes minutes; OpenClaw is
configured with a 300 s request timeout to accommodate it.

### Tests

`python -m unittest discover -s tests -t .` — 709 tests, all offline (mocked HTTP,
temp SQLite).

Covers pricing/VAT, analytics, DB, config, connectors, supplier connectors,
Fueltool/Fuelsoft parsing, browser auth, Brent, spreadsheet import, email
monitoring, and end-to-end app wiring.

---

## Database (as of 2026-09-22)

- **Path:** `data/oilwatch.sqlite`
- **Suppliers:** 28 (17 `active`; the rest historical, including Brogan Fuels,
  retired 2026-09-18 as part of Scottish Fuels)
- **Quotes:** 630
- **Orders:** 1
- **Quote requests:** 6 — the asks written down, each with the `channel` it was
  made by (`form` or `email`) and an `answered_at` for one a price closed (added
  2026-09-22; the ledger note under Automation status has the reasoning)

**Added 2026-09-10 — purchases can be recorded.** The `orders` table was
write-only: `place_order` wrote to it but nothing ever read it back, so "who did
I buy from last time, and what did I pay" had no answer, and no agent could see
it. `oilwatch record-purchase` now writes down a purchase the owner made
themselves (supplier by name fragment or id, price per litre *or* total paid,
optional discount code and reference), with an ambiguous name refused rather
than guessed. `oilwatch purchases` and the read-only MCP `purchases` tool read
them back, and `status` carries the last one. Nothing in this path drives a
browser or contacts a supplier — recording is kept separate from buying.

Quote timestamps span **2007-01-26 → 2026-09-22**, because
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
- **Verified 2026-09-12 (browser, live):** the Fuelsoft form priced all three of
  its suppliers in one pass — Connon Bros £1.2646/L, Johnson Oils £1.2226/L,
  Regency Oils £1.2057/L inc-VAT — so the earlier "no price" was the frozen
  `Chrome/122` user-agent, not the form (fixed in 9d27a11). BoilerJuice's browser
  connector now authenticates with the stored account and reads a price: its
  sign-in path had been a 404 (`/uk/login` -> `/uk/users/login`), the Cookiebot
  consent dialog hid the form, and both the signed-in marker and the Get-Quote
  button were matching hidden elements. The price comes from the cheapest
  inclusive `You Pay £…` total (ex-VAT fuel + VAT + the broker's service charge,
  the total the supplier note names) — £1.1957/L inc-VAT for 1000L on the day —
  not from the headline `ppl`, which is ex-VAT and omits the charge.
- **An ask is written down now, so silence can be told from a reply
  (2026-09-22).** A supplier replies by hand, which made "we asked and they have
  not answered" indistinguishable from "nobody ever asked" — in the database as
  much as in an empty mailbox. `submit-requests` now records every form it
  submits, and a new `quote_requests` table holds the ask with the `channel` it
  was made by, the quantity, the postcode and an `answered_at`; a price from that
  supplier closes its request, and `status` reports what is still owed as
  `awaiting_reply`, oldest first, beside `no_quote_suppliers` — the last ask that
  came back empty. Six are on record from the first run.
- **A supplier with no form is asked by email (2026-09-22).**
  `submit-requests --by-email` writes to the register's suppliers that have no
  form but do carry an address, under the marked subject
  `oilwatch quote request - <postcode> - <litres>L - <date>` so a reply is
  findable, and records the ask as `channel: "email"`. Sending needed the
  mailbox's `Mail.Send` scope added to the Entra app and re-consented through
  `oilwatch login-email`; a reply is placed by sender domain, so
  `compassfuel.co.uk` (Compass answers from a domain one letter shorter than its
  website) and `turriff-fuels.co.uk` (Turriff's mail keeps the hyphen its website
  dropped) both map to their rows rather than sitting unread.
- **The phone route is gone — the app asks by form or by email, never rings
  (2026-09-22).** Wayne's decision: OilWatch will never phone a supplier. The
  route had been half-present since the start — a call-sheet command generated a
  dial list, a telephone "connector" was registered, `order_channel` had
  `phone` and `phone_email` values, and a register record marked
  `{"phone": true}` came back from `submit-requests` as "call this number". All
  of it is removed rather than left as a thing nobody uses: a route the app
  still describes is a route an agent may still take. What the vocabulary says
  now is one thing — a supplier is asked by its quote form, or by email at the
  address on record; a supplier with neither is *not asked*, and says so, rather
  than being reported as a number to ring. `order_channel` loses `phone` and
  `phone_email` (becoming `web`, `email`, `benchmark`, `none`, with a number
  alone reporting `none`) and `requests_from` returns its form keys instead of
  a second list of calls. The register key `quote_request.phone` is renamed
  `quote_request.no_form`: it always meant "this one has no form", and reading a
  phone number off a flag about forms is what let the two drift. Phone numbers
  stay everywhere they are *data* — the register's `phone` field, the DB column,
  `contact.phone` — and the `--phone` flags that carry the owner's own number
  into a supplier's form are untouched. **Amended the same day: a note is not a
  route either.** Five notes still led with "Contact via: <number>, …" (the
  generic manual fallback — Carnegie Fuels, Crown Oil, Gleaner Oils, Nationwide
  Fuels and Turriff Fuels), and Oilfast's, Rix's, Scottish Fuels' and Regency
  Oils' own notes listed the number first, as did three `order_notes` strings
  that said "or by phone". A note is read by the same agents that read `status`, so
  the number is out of every note; it stays in the register, the DB column, the
  `contact.phone` field and each connector's `raw_payload`. Counts move with it:
  CLI 21 → 20, supplier connectors 15 → 14, modules under `oilwatch/` 55 → 54.
- **The sweep asks a model only about the mail its patterns miss (2026-09-22).**
  An unrecognised sender was named in the log only when `extract_ppl` found a
  price, which is a regex standing in for a judgement about meaning: a genuine
  quote in an uncovered format read as "not fuel", so the one case the alert
  exists for was the one it stayed quiet about. The question is now put to
  TypeSafe's Jev — `extract_ppl` still runs first, being free and exact — held to
  a probability (`fuel_mail_min_probability`, 0.8) and cached per sender domain,
  so the unresolved mail left in the mailbox costs one request, not one per sweep.
- **BoilerJuice's journey needs two selects answered (2026-09-22).** Its quote
  page would not price until the oil type and tanker size were chosen — both
  marked `required`, so the submission was rejected without saying why — and the
  no-price failure now names what the page actually held rather than only that it
  carried no price. With the selects answered the journey prices again.
- **No published price, but reachable (2026-09-22).** Turriff Fuels and Carnegie
  Fuels have neither a quote form nor a published price, so nothing can be
  scraped — but both carry an address (Turriff's, `rory@turriff-fuels.co.uk`, was
  on its contact page), so `--by-email` asks them and no quote depends on a phone
  call.
- **Quote by request (2026-09-18):** Oilfast Insch, Compass Fuels, Gleaner Oils,
  Nationwide Fuels and Crown Oil each have a live quote page that answers a
  person rather than the app — a wpforms form, a Gravity Forms pair, or a lead
  form behind Turnstile — so they carry `reason: quote_by_request` and their
  `order_page` in the register, not `no_quote_page`. The register itself is
  `config/suppliers.json`: the suppliers it knows, the domains never to treat as
  one, and each supplier's `quote_request` saying whether it is asked by form —
  where it has no form, an address on the record is what the app writes to. It is
  version controlled; `config/settings.json` is not, and holds
  the owner's personal values and operational settings — address, postcode,
  credentials, the freshness window, the order quantity — but no supplier policy.
- **Rows say what they are and how to act on them (2026-09-18).** Each priced row
  now carries `kind` (`supplier`/`benchmark` — Fueltool's record sets it, and the
  new column defaults to `supplier` for every other row), `order_channel` (`web`
  when an `order_page` is recorded, else `email`, or `benchmark`, or `none` when
  neither is recorded — a phone number alone lands there since 2026-09-22, the
  app never ringing a supplier) and a `contact` of
  `{phone, email, url}` where `url` is the ordering page when there is one. The
  winner carries the same three. This is the half of Hal's §2.3 ask that mattered
  most: "Fueltool is a benchmark, never the winner" and "give the ordering URL"
  were rules enforced by reading prose and remembering, and are now fields.
- **A sweep says whether it is still running (2026-09-18).** The part of Hal's
  §2.4 ask that the cooldown did not cover: after a client's own call times out,
  it could not tell *still running* from *died*. A sweep now marks itself in a
  `sweeps` table before the first browser opens and clears the mark however it
  ends, and `current_prices` / `status` carry a `refresh` block —
  `in_progress` with `started_by` and `seconds_ago`, or `stale` when a mark
  outlived any sweep (10 minutes) and the run never reported back.
  `refresh_prices` checks it *before* the cooldown and starts nothing when one is
  running, which closes a real hole: a running sweep's own rows appear only as
  each supplier finishes, so thirty seconds in the cooldown had nothing to
  measure and a retry would have relaunched every browser.
- **The job model Hal asked for, as a detached worker (2026-09-18).**
  `refresh_prices(background=true)` writes the job row, spawns
  `quote-all --job-id <id>` as its own process from the checkout, and returns the
  id at once; the worker reports `done` of `total` as each supplier lands and
  closes the job `finished` or `failed`. `refresh_status` reads it by id or
  newest, with `stale: true` for a job whose worker is gone — the same honest
  reading the sweep marker gives. The sweep now records each quote as it lands
  rather than after the slowest supplier, which is what makes mid-flight progress
  possible; the writes stay in the one thread, so the SQLite contention the
  original comment guards against is untouched.
- **The email sweep's counts reconcile now (2026-09-21).** Three branches dropped
  a message without a word, so `sweep: scanned 187, recorded 0, 186 from
  unrecognised sender(s)` appeared hourly with one message unaccounted for. Found
  by classifying the live mailbox rather than by reading the sweep's summary: the
  message was a HomeFuels Direct newsletter already in Deleted Items, and the
  "nothing to record" log line sat *inside* the branch that deletes, so it only
  spoke when it had something to delete. Two smaller ones are now named too — a
  sender domain that maps to a supplier fragment no row carries (a warning), and
  a duplicate observation (an info line, expected when a reply's id changes on a
  folder move). Nothing was being missed: the newsletter carried no price and no
  code, so the fix is the log's integrity rather than a recovered quote — but a
  sweep whose arithmetic does not add up is a sweep nobody can audit.
- **Every result can now say why it is not `ok` (2026-09-18).** The last gap Hal
  had flagged: ten suppliers' latest attempts carried `reason: null`, because the
  sites producing them had never been given a value — the HTTP connectors whose
  quote form needs driving, and the parse fallbacks that found nothing. Filling
  them in needed a vocabulary decision, so two values joined the set:
  `browser_required` (this path cannot price it and browser automation can — Rix,
  Scottish Fuels, Regency Oils, which `no_quote_page` would have misstated and
  `site_error` would have libelled) and `no_price_found` (the page answered and
  carried no price, as against `site_error` where the attempt raised — the
  distinction that tells a reader whether to retry or to look at the connector).
  All 15 manual and 10 error sites now carry one. `QUOTE_REASONS` in `models.py`
  is the set, `tests/test_docs.py` fails if the README or AGENTS.md stops naming a
  value, and `tests/test_connectors.py` parses the package for a `QuoteResult`
  with a non-`ok` status and no reason, so the next connector cannot reintroduce
  the gap. The rows already on record keep their nulls: they are older than the
  reasons, and rewriting them would falsify what those attempts actually said.
- **On demand, not on a timer (changed 2026-09-15).** Prices are refreshed when
  someone asks for them — `oilwatch quote-all --browser`, `refresh_prices` over
  MCP, or an agent turn — rather than on a schedule. The scheduler's per-user
  Startup entry (`…\Startup\OilWatch Scheduler.bat`) was therefore renamed to
  `.disabled`, at Wayne's direction: *"we should only start the scheduler when we
  want new prices … on a cli or agent request basis, not a scheduled basis."* The
  capability is unchanged, so `oilwatch schedule` and `start_scheduler.bat`
  remain for a deliberate run, where quotes follow `config/settings.json`
  (daily), discovery weekly, and the email sweep hourly on weekdays.
  **Resolved 2026-09-15:** the entry had been enabled (`StartupApproved` `02`)
  and no `oilwatch schedule` process was running; it was found while checking
  something else, and the policy above settled it. Note that an empty
  `data/oilwatch.log` proves nothing about whether the scheduler ran — a file
  opened in append mode does not move its mtime, so only a *written* record does.
- **The email sweep is still scheduled, and is a separate mechanism.** Its own
  Task Scheduler entry (`OilWatch Email Monitor`, hourly, **08:00-23:00
  Mon-Fri**) is unaffected by the scheduler decision above; `monitor_email.bat`
  runs one sweep by hand. It is kept as a fallback: the sweep is idempotent, so a
  double run costs nothing. **Changed 2026-09-15:** asked whether the on-demand
  principle should cover it too, Wayne kept it scheduled but widened the weekday
  window to 23:00 — a supplier's reply to a quote request arrives whenever the
  supplier chooses, so polling is the only way to notice it without a human
  looking, and holding the window open past the working day is what lets an
  evening reply be recorded the same day. The change was to the repetition
  `Duration` only (`PT11H` → `PT15H`), leaving the hourly interval, the weekly
  Mon-Fri schedule and the action untouched; the previous definition was exported
  before the edit. ⚠️ The task is registered **`Stop On Battery Mode, No Start On
  Batteries`**, so on battery an unattended sweep silently does not run — reported
  to Wayne rather than changed, since whether to poll on battery is his call.
  **Resolved 2026-09-13:** a
  second, redundant task (`Oilwatch Monitor Email`, daily at 08:00) was deleted —
  its `/TR` was byte-identical to the hourly task's, so the hourly run already
  covered it. The surviving fallback is `Oilwatch Email Monitor`; the two
  near-identical names are worth reading twice for that reason. **Resolved
  2026-09-13:** both sweeps were then confined to **08:00-18:00 Mon-Fri**, because
  heating-oil suppliers are shut at weekends and overnight, so those runs could
  only read an inbox the next in-window run reads anyway. The sweep's job is
  a weekday cron rather than an open interval (`oilwatch/scheduler.py`, built
  from `email_monitor_start_hour` / `_end_hour` / `_days`); the task is a weekly
  Mon-Fri trigger with `Interval PT1H` and `Duration PT15H` (`PT11H` until the
  2026-09-15 widening above). The sweep is a poll,
  so a reply arriving outside the window is still recorded at its own
  `receivedDateTime` when the next in-window run finds it — but one arriving
  after Friday's last sweep lands ~2.6 days old and so falls outside the 1-day
  `max_quote_age_days` window. Two schtasks
  traps met on the way: `/Create` works unelevated for `HOURLY`/`DAILY` but not
  `ONLOGON` (Access denied), which is why logon autostart uses the Startup
  folder; and its `/TR` path must be quoted for the shell — `\"…\"` under
  `cmd.exe`, `'"…"'` under PowerShell — or it splits at the first space.
  **Logged 2026-09-13:** the unattended runs write a rotating log to
  `data/oilwatch.log` (1 MB, 3 backups), because Task Scheduler and a
  minimised Startup window leave no console to read a failure from. The path is
  written once, in `oilwatch_env.bat`, which both launchers call — a copy in each
  is the same drift the settings pair is guarded against, and `tests/test_docs.py`
  fails if a launcher defines it again. It is an environment variable rather than
  a `config/settings.json` key, which keeps the test suite and interactive runs
  out of the log: a hand-run `oilwatch status` still logs to its console only.

**Fixed 2026-09-15 — the Scottish Fuels sign-in, and what actually breaks it.**
The automatic sign-in is *intermittent*, and every failure simply read "the
sign-in did not take", so there was nothing to act on. A Chrome DevTools
performance log showed what is really happening: clicking Sign In often
**submits nothing at all** — `button#send2` returns in 0.1 s, the URL never
changes, and **no POST ever reaches `/customer/account/loginPost/`**. The site's
own submit handler swallows the event, so "did not take" meant *nothing was
submitted*, not *the credentials were rejected*. Two consequences were fixed.
`_submit_sign_in()` used to return `True` as soon as an interaction did not
*raise*, which left its Enter and JavaScript-click fallbacks unreachable in
exactly the case they exist for; it now plants a page marker and requires the
page to actually navigate before believing a submit, falling through to the next
interaction when it does not. And page loads were unbounded — a sign-in stalled
past 15 minutes — so `launch()` now sets a 60 s page-load timeout. A failure is
now self-describing: landed URL, title, whether the login form is still present,
the reCAPTCHA field *lengths* (never token values) and the page text. **The
site-side intermittency itself is not fixed** — expect `manual_action_required`
for this supplier sometimes, from the CLI as much as from MCP. The served login
page carries three forms and three submit buttons; field selection was checked
live and is correct.

**Fixed 2026-09-15 — `api-discover` ignored an explicit `--url`.** Given both
`--url` and `--supplier-id`, the supplier row overwrote the URL, silently
discarding the flag the user had just typed; the row is now consulted only when
no URL was given. This surfaced from `tests/test_untested_modules.py`, added to
close the last uncovered handler paths: the call-sheet command's loop and
its `--output` export, `--supplier-id` resolution, `register`'s summary and its
`--output` save, and `login-email`'s reporting. The call-sheet tests went with
the command on 2026-09-22 (see the phone-route note above); the rest stand.

### MCP / OpenClaw integration

The server is registered in OpenClaw (`~/.openclaw/openclaw.json`) as
`mcp.servers.oilwatch`, as an **on-demand stdio** server: OpenClaw spawns

    .venv\Scripts\python.exe -m oilwatch.mcp_server --stdio

(from WSL, `/mnt/c/.../.venv/Scripts/python.exe`) when it needs the tools, and
the process dies with the session. WSL launches the Windows venv's python through
interop; the module is editable-installed and `main()` resolves its root from
`__file__`, so no working directory is needed. `--stdio` is the transport added
for this; HTTP remains the default for `start_mcp_server.bat` and manual runs.

Verified 2026-09-12 with OpenClaw 2026.9.3: `openclaw mcp probe oilwatch`
reports **9 tools, resources, prompts**, and `openclaw mcp doctor` reports
`oilwatch: ok`. The stdio `initialize` handshake returns
`serverInfo: {"name":"oilwatch","version":"0.1.0"}` — the package's own version,
not the MCP framework's (which is what it reported before the server declared
one).

**Known defect, 2026-09-16 — a missed `initialize` budget kills the whole
OpenClaw gateway.** Not an OilWatch bug, and not fixable from this repository.
When this server does not finish the stdio handshake inside the client's budget,
OpenClaw logs `failed to start server "oilwatch" ... did not complete initialize
within Ns`, and 0.25 s later throws an unhandled rejection out of its
child-cleanup path (`service child cleanup identity lost: anchor channel closed
without a matching closing receipt`, at `loseIdentity` ← `finishPosixAuthority` ←
`Socket` close), writes a stability bundle, and exits `status=1/FAILURE` — taking
every child in its cgroup with it: the other MCP servers, `openclaw cron` jobs in
flight, heartbeat scripts. It was observed nine times between 2026-09-11 and
2026-09-16.

It is not specific to this server. `outlook`, also a spawned stdio child,
produced the identical rejection on 2026-09-15 after its own init timeout, so
retiring this one would leave the path open. A remote streamable-HTTP server
cannot reach it at all: `graphify` timed out four times on 2026-09-16 with no
rejection — the one structural answer, at the cost described under "Superseded"
below.

The budget is `connectionTimeoutMs`, confirmed as the knob by three configured
values matching the seconds their log lines printed. Both stdio servers here now
carry **180000**. Size it on the cold tail, not on a warm median: a warm
`initialize` measures ~1.6-2.4 s, while a cold filesystem cache is several times
slower (~8.7 s reported). The WSL interop path is not the variable — spawning
this Windows python from inside WSL over `/mnt/c` measured the same as a direct
Windows spawn.

The 2026-09-16 import tidy-up (commit `abdf508`, matplotlib moved off the
server's import path, ~0.6 s of the cold start) is a cleanup standing next to
this, **not** the remedy. The upstream work is OpenClaw #144941
(`fix(doctor): contain MCP child cleanup failures`, merged 2026-09-11T15:49Z).
Despite the title, it is **not** doctor-only: it wraps `client.close` inside the
shared `connectMcpClient`, so the promise `Client.connect` discards gets a
handler — the same path agent runs and node hosts use, and #144911's thread reads
its evidence as covering Gateway connections too. What is missing is
**verification, not scope**: no maintainer confirmed Gateway coverage, the PR's own
deployment validation was explicitly deferred, and no release carries it (the
2026.9.4 tag, 03:46Z, predates the merge and is still npm's latest). #144911
remains open, P1, `impact:crash-loop`. Our crash was on 2026.9.4, so it neither
confirms nor refutes the fix.

Stronger than that, and checked against the source rather than the thread: **the
call site that produced our crash is not in #144941's diff at all.** The PR changes
five files — `docs/gateway/doctor/checks.md`, `src/agents/mcp-client-lifecycle.ts`,
`src/agents/mcp-stdio-transport.test.ts`, `src/flows/doctor-core-checks.runtime.ts`
and its test — and **nothing under `src/process/supervisor/`**. On `main`, the anchor
control channel's close handler still calls `void finishPosixAuthority(...)`: an
`async` function whose rejection is therefore unhandled, whose fallback message is
literally "anchor channel closed without a matching closing receipt". So
"verification is missing" understates it — even once #144941 ships, the fatal path
is not provably closed. Cite that handler, not a line number: the file moves (one
report cites 563-569, another 525).

**Fixed 2026-09-15 — `refresh_prices` over MCP, which used to hang for ever.**
Called through mcporter it returned **zero output** and timed out twice, even at
900 s, with no DB mtime change — the failure that had made the CLI look like the
only working door, and that got written up elsewhere as "the MCP route is a dead
end". The cause was in this repository, not the transport: `BrowserAuth.launch()`
passed `use_subprocess=False` to `undetected-chromedriver`, whose
`start_detached()` starts Chrome through a **`multiprocessing` spawn child** and
then blocks on `reader.recv()` **with no timeout and without ever closing its own
copy of the pipe** — so a helper that misbehaved produced an infinite hang rather
than an error. Inside the long-running threaded server that helper never booted
Python at all (no `_multiprocessing.pyd` loaded, ~0.03 s CPU, no output), so no
Chrome was ever started and the whole sweep wedged on the first supplier. The
same sweep in a plain process always completed in ~35 s. `use_subprocess=True` —
uc's own default — starts Chrome with a plain `subprocess.Popen`, removing the
multiprocessing child entirely. Verified through this same stdio server:
`refresh_prices` returned in **67 s** with a full quote list, Scottish Fuels
included, and wrote it to the database. `tests/test_browser_auth.py` pins the
launch mode, the page-load bound and the submit verification.

**Superseded:** until 2026-09-12 this was a streamable-HTTP endpoint OpenClaw
dialled at `http://172.28.144.1:8000/mcp` — the WSL **NAT gateway**, because WSL
cannot reach the Windows host on `localhost` and the working address depends on
the WSL networking mode (NAT now; the LAN IP `192.168.33.56` under mirrored).
That required the server to be running *and* the address to be right, so either
slipping produced a `did not complete initialize within 10s` doctor warning. The
stdio entry removes both preconditions — but not for free: it is what exposes
this server to the fatal init-timeout above, which the HTTP transport could not
reach.

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
- **Resolved 2026-09-22:** the counts drifted once more, in the places nothing
  reads. `refresh_status` became the tenth MCP tool on 2026-09-19 and the
  test-derived places moved with it, but the prose did not: `AGENTS.md`'s trap
  list, `user-guide.md` §14 and three rows of `docs/inspection.md` still said
  nine, and inspection's expected tool list had stopped at nine names. All six now
  say ten, with `refresh_status` restored to the list. The count is read from the
  served object (`mcp.list_tools()`), not by spawning the stdio child.

---

## Next actions

1. **DONE — the database is the source of truth** (decided 2026-09-10). The
   workbook is retained only as a historical import: the hardcoded `P:\` path is
   gone and `import-spreadsheet` now requires an explicit `--path`.
2. **DONE 2026-09-12 — there is no OpenClaw `oilwatch` URL to keep valid.** It
   pointed at the WSL NAT gateway (`172.28.144.1`), so the server had to be
   running and the address right. OpenClaw now spawns the server on demand over
   stdio, so neither precondition applies.
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
4. **DONE 2026-09-11, retired 2026-09-12 — the MCP server started at logon.** A
   per-user Startup entry (`%APPDATA%\Microsoft\Windows\Start
   Menu\Programs\Startup\OilWatch MCP Server.bat`) ran `start_mcp_server.bat`
   minimised at every logon (a Startup entry rather than a Task Scheduler task
   because creating one needs elevation — `schtasks /Create /SC ONLOGON` returned
   Access denied). Once OpenClaw moved to spawning the server on demand over
   stdio, nothing dialled the HTTP endpoint any more, so the entry was disabled
   by renaming it to `OilWatch MCP Server.bat.disabled`; `start_mcp_server.bat`
   remains for serving HTTP by hand. **Resolved 2026-09-13:** the renamed file
   was then deleted. A `.disabled` suffix is not an executable extension, so
   Windows had not launched it since the rename; `openclaw mcp probe oilwatch`
   again reported 9 tools, resources, prompts over the on-demand stdio path.
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
6. **DONE 2026-09-22 — a supplier is asked by form or by email, never by
   phone.** A supplier is asked in exactly two ways: the register's quote forms
   are submitted, or `submit-requests --by-email` writes to the ones with no form
   but an address. A supplier with neither is not asked at all: it is reported as
   unasked and stays out of `awaiting_reply`, because the app never asked it. Its
   phone number is data, not a route — a number alone reports
   `order_channel: none`. **Amended the same day:** the first version of this
   reported such a supplier as a number to ring, which was the tail of a phone
   route now removed entirely (the call-sheet command, the telephone connector, the
   `phone` / `phone_email` channels and the `quote_request.phone` register flag).
   **The owner's own number is unaffected:** a supplier's form that requires a
   phone number still gets his, through `--phone` — that is a form being filled
   in, not a call being made, and it is written down so it is not stripped later
   as leftover phone-route code.
