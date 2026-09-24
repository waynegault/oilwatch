# AGENTS.md — the rules an agent needs

The consumer contract for OilWatch, for any agent driving its CLI or MCP server.
`user-guide.md` §14 is the full brief; this file is the part that changes
behaviour, so it is worth reading even when nothing else is. The full tool
reference — every flag and return shape — is in `README.md`, along with the
verified counts and what this tool deliberately does not do.

## What you can call

**Over MCP — ten tools.** Reads: `list_suppliers`, `current_prices`, `cheapest`,
`purchases`, `status`, `refresh_status`. Write a file and return its path:
`chart`, `time_series_chart`. Costs minutes: `refresh_prices`. Reaches the
network: `update_brent`.

**Through the CLI — twenty-one commands**, as `oilwatch <command>` or
`python -m oilwatch.cli <command>`: `init`, `discover`, `suppliers`, `duplicates`,
`quote`,
`quote-all`, `cheapest`, `status`, `chart`, `time-series`, `update-brent`,
`import-spreadsheet`, `record-purchase`, `purchases`, `schedule`, `api-discover`,
`register`, `login`, `submit-requests`, `monitor-email`, `login-email`.
`oilwatch --help` gives the flags; `README.md` is the reference.

A few of these have consequences beyond this machine, so call them
deliberately: `quote-all` and `refresh_prices` drive real browsers,
`submit-requests` puts the owner's details in a supplier's form — or, with
`--by-email`, in a message sent from the owner's own mailbox —
`monitor-email` reads the mailbox and deletes what it processes, and
`record-purchase` is the owner's own act.

## Reading prices

- **"What are prices today?" means refresh, then read.** Nothing refreshes on a
  timer — a refresh is an explicit act: `refresh_prices` over MCP, or
  `oilwatch quote-all --browser`.
- **A quote stands for at most one day** — a ceiling, not an average. A thin read,
  or one naming `excluded_suppliers`, is a stale snapshot rather than a scrape
  failure.
- **Never loop `refresh_prices`.** It takes 1–3 minutes. If it times out, read
  `current_prices` and check `observed_at` instead of re-running, because a re-run
  launches browsers again. Its `refresh` block says whether a sweep is **still
  running** (`in_progress`, with `started_by` and `seconds_ago`), has finished, or
  started and never reported back (`stale`) — so you never have to guess, and a
  `refresh_prices` while one is running starts nothing.
- **If you expect to outlive the call, pass `background=true`.** It returns a
  `job_id` at once and runs the sweep as its own detached process; read
  `refresh_status(job_id)` for `state`, `done` of `total`, and the `results`. A
  `stale: true` there means the worker is gone — treat it as failed rather than
  waiting.
- **Always give the ordering link, the discount code, the `observed_at` date and
  the basis** (£/litre inc. 5% VAT, 1000 L) next to a price. Prefer a row's
  `order_page` when it carries one; `website` is often only a marketing page.
- **Fueltool is a UK-average benchmark, not a supplier.** Never present it as the
  winner.

## Normal, not a fault

- `manual_action_required` **with contact details** is expected, not a failure.
- A **partial result is normal**: `refresh_prices` fails per supplier.
- The **Scottish Fuels sign-in is intermittent.** It fails identically from the
  CLI, so it is not an MCP problem.

## What a row's `status` can be

A quote row's `status` is a closed set of three, and a consumer branches on it:
**`ok`** (a price was read), **`manual_action_required`** (no price the app can
read — the supplier is contactable instead) and **`error`** (the attempt itself
raised). `price_per_liter` and `total_price` are `null` for everything except
`ok`. Do not confuse it with a supplier row's `status`
(`active`/`inactive`/`manual_review`) or with the result of a form submission,
which uses `submitted` (the page acknowledged it and the ask is on record),
`unconfirmed` (it did not, so nothing was written to `quote_requests` and it will
not appear in `awaiting_reply`), `pending` and so on.

## When a price is missing, read its `reason`

Every row that is not `ok` carries a machine-readable `reason` beside the prose in
`notes`, so a gap can be explained without parsing English:

- `no_quote_page` — no web quote exists at all: ask by email.
- `quote_by_request` — a quote page exists, but it answers a **person**: it takes
  the details and replies, so there is no price to read. Say this, and not "no
  quote page" — the two send a reader to different places.
- `browser_required` — this path cannot price the supplier and browser automation
  can: quote it with a browser rather than by hand.
- `no_price_found` — the page answered and carried no price; `site_error` is the
  other one, where the attempt raised. Which of the two says whether to retry or
  to look at the connector.
- `login_not_confirmed` — an authenticated portal did not sign in.
- `captcha` — a bot check stopped the flow.
- `site_error` — the attempt raised: a timeout, an HTTP error, or a parse failure.
- `null` — unclassified, not "no reason": no connector returns it for a new
  result any more, so a row carrying it is older than the day the reasons were
  filled in, and says nothing about the supplier.

## Who is being asked, and where

The supplier register is **`config/suppliers.json`**, which is version controlled
— unlike `config/settings.json`, which is gitignored. That file holds the owner's
personal values and operational settings (address, postcode, credentials, the
freshness window, the order quantity) but no supplier policy. The register holds
the suppliers, the domains never to treat as one, and each supplier's
`quote_request` saying whether it is asked by form; where there is no form, an
address on the record is what the app writes to. So it, and not the code, is the
list of suppliers this install chases: read it before saying who is or is not
being asked.

A row's **`order_page`** is where a quote is actually requested; its `website` is
often only a marketing page. Priced rows also carry **`kind`**
(`supplier`/`benchmark`), **`order_channel`** (`web`, `email`, `benchmark`,
`none`) and a **`contact`** of `{phone, email, url}`, so
"who can I buy from, and how?" is answered by fields rather than by reading notes.
Use `contact.url` — the ordering page when there is one — and treat a `kind` of
`benchmark` as a figure (Fueltool), never a winner to report.

**"Are we waiting on a reply?" is `awaiting_reply` in `status` — a different
question from "is a price missing".** `no_quote_suppliers` names the last ask that
came back empty; `awaiting_reply` names the asks still owed an answer, oldest
first, each with the `channel` it was made by. A supplier replies by hand, so an
ask that was never written down is indistinguishable from one still being
considered, in the database as much as in an empty mailbox.

**Two ways to ask, and only two.** `submit-requests` submits the register's quote
forms and records each one (`channel: "form"`); `submit-requests --by-email`
writes to the suppliers that have no form but do carry an address
(`channel: "email"`). Either way a price from that supplier closes the ask.
**The app never rings anyone:** a supplier with no form and no address is left
unasked — reported as such, with no number offered, and kept out of
`awaiting_reply`, because the app did not ask it.

**A supplier's form may ask for the owner's phone number; give it.** `--phone`
exists to fill that field, because the form will not submit without it. That is a
form being filled in, not a supplier being telephoned — do not strip it as
leftover phone-route code.

## Never

- **Never record a purchase from a price.** Recording is the owner's deliberate CLI
  act (`record-purchase`); the `purchases` tool only reads back.
- **Never edit this repository to work around a supplier problem.** Report what the
  tool said and let the owner decide.
- **Never pass a `/mnt/c/...` path as an *argument*** to the Windows python — WSL
  interop translates the executable path but not argument paths.
- **Never try to fix the gateway crash here.** A stdio child that misses its init
  budget can take the whole OpenClaw gateway down with a child-cleanup rejection;
  that is an OpenClaw defect, tracked upstream, not this repository's problem.

## Traps in the transport

- The tools are served over **stdio, spawned per session**: there is no port to
  check and no server to start. `openclaw mcp probe oilwatch` should report
  **10 tools**.
- The repo path contains a **space**, so a client that word-splits the stdio
  command fails with `spawn .../Projects/Oil ENOENT`; give such a client a
  space-free wrapper. `user-guide.md` §Traps has the working forms.
