# AGENTS.md — the rules an agent needs

The consumer contract for OilWatch, for any agent driving its CLI or MCP server.
`user-guide.md` §14 is the full brief; this file is the part that changes
behaviour, so it is worth reading even when nothing else is. The tool reference is
in `README.md`, and status in `PROGRESS.md`.

## Reading prices

- **"What are prices today?" means refresh, then read.** Nothing refreshes on a
  timer — a refresh is an explicit act: `refresh_prices` over MCP, or
  `oilwatch quote-all --browser`.
- **A quote stands for at most one day** — a ceiling, not an average. A thin read,
  or one naming `excluded_suppliers`, is a stale snapshot rather than a scrape
  failure.
- **Never loop `refresh_prices`.** It takes 1–3 minutes. If it times out, read
  `current_prices` and check `observed_at` instead of re-running, because a re-run
  launches browsers again.
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
  `PROGRESS.md` has the detail.

## Traps in the transport

- The tools are served over **stdio, spawned per session**: there is no port to
  check and no server to start. `openclaw mcp probe oilwatch` should report
  **9 tools**.
- The repo path contains a **space**, so a client that word-splits the stdio
  command fails with `spawn .../Projects/Oil ENOENT`; give such a client a
  space-free wrapper. `user-guide.md` §Traps has the working forms.
