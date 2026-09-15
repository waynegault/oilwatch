# OilWatch: a brief for an agent (OpenClaw / Hal)

Paste the block below into an agent that has this machine's MCP server configured.
It is written to be self-contained: the agent does not need the repository
checked out or the code read to use OilWatch correctly.

---

You have **OilWatch** — a heating-oil price tracker for Wayne's delivery address
(Hatton of Fintray, AB21 0YA, Aberdeenshire). It is a Python project at
`/mnt/c/Users/wayne/GitHub/Python/Projects/Oil Price Webscraper`, reachable from
WSL through the Windows venv python at `.venv/Scripts/python.exe`.

There are two doors onto the same engine. Use them like this.

## Reads — the `oilwatch` MCP tools (all instant, no browser)

- **`current_prices`** — the latest quote per supplier (£/L inc. VAT). Suppliers
  with no quote inside the freshness window are *omitted*, and `cheapest` /
  `status` name the ones that were held back.
- **`cheapest`** — the winner, plus the market average and variance.
- **`status`** — the snapshot, the trend, and a buy/hold read.
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
  landed, and only then decide whether to run it again. Optionally pass a
  `postcode`; otherwise the configured delivery address is used.

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

`oilwatch.cli --help` lists all 21 commands.

## Traps

- **Never pass a `/mnt/c/...` path as an *argument*** to the Windows python: WSL
  interop translates the executable path but not argument paths. `-m` needs none.
- **A client that builds the stdio command by splitting it on spaces cannot use
  this path.** The repo lives under `Oil Price Webscraper`, so a client that
  word-splits the command sees `.../Projects/Oil` and fails with
  `spawn .../Projects/Oil ENOENT`. Give such a client a space-free wrapper (a
  launcher that execs this venv's `python -m oilwatch.mcp_server --stdio`)
  rather than the raw path.
- The MCP server is **spawned per session over stdio** — there is no port to
  check and no server to start. If the tools look unhealthy,
  `openclaw mcp probe oilwatch` should report **9 tools**.
- Don't edit this repository to work around a supplier problem. Report what the
  tool said and let Wayne decide.
