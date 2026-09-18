# OilWatch Inspection, Improvement & Audit Checklist

A repeatable checklist for auditing the OilWatch package, its connectors, its
configuration, its docs and its test guards. Every item carries the rationale, a
concrete command, and the expected outcome, so a pass produces evidence rather
than an opinion. Derived from real audits, including the 2026-09-15 pass that
found a credential leak, a working-directory defect and a wrong-quantity quote.

## Scope

In scope for every audit pass:

- `oilwatch/*.py` — the package (CLI, MCP server, service, DB, analytics,
  pricing, identity, credentials, discovery, geo, scheduler, email)
- `oilwatch/connectors/` — `base.py`, `browser_base.py`, `sync_browser.py`,
  `manual.py`, `price_page.py`, `http_form.py`
- `oilwatch/connectors/suppliers/` — the per-supplier connectors and the
  `_CONNECTORS` / `_SUPPLIER_CONNECTORS` registries
- `tests/*.py` — the suite, and in particular `tests/test_docs.py`, which guards
  the documented counts
- `config/settings.example.json` — the shipped example (the live
  `config/settings.json`, `contact.json` and `supplier_credentials.json` are
  gitignored: read for correctness, never commit)
- `*.bat` launchers — `oilwatch_env.bat`, `start_mcp_server.bat`,
  `start_scheduler.bat`, `monitor_email.bat`
- `pyproject.toml` — dependencies, coverage floor, console scripts
- `README.md`, `AGENTS.md`, `user-guide.md`, `docs/inspection.md`, `PROGRESS.md`,
  `ROADMAP.md`
- `data/oilwatch.sqlite` and `data/oilwatch.log` — generated, gitignored: read
  for content, never commit
- `.gitignore`

Out of scope: `.venv/`, `graphify-out/`, `.qwen/` (local scratch),
`data/*.png` (generated charts).

## Usage

Work top-to-bottom. Mark each item `[x]` as it passes. Items are marked:

- 🔧 the item requires a code or config change when it fails
- 🔍 the item is a read-only check

Commands are shown in the repository root. `grep -rn` means "search the tree" —
on Windows use `findstr /s /n /i`, or an agent's own search tool; the pattern is
what matters, not the binary.

---

## Table of Contents

1. Pre-Flight
2. Security — Critical
3. Safety — Critical
4. Correctness — High
5. Robustness — High
6. Efficiency & Concurrency — Medium
7. Configuration & Path Resolution — High
8. Style & Conventions — Medium
9. Documentation & Guards — Medium
10. Testing — High
11. MCP & AI Agent Access — High
12. Data & Storage — Medium
13. Supplier Connector Accuracy — High
14. Final Validation
15. New Insights & Standards (2026-09-15)

---

## 1. Pre-Flight

Establish a baseline before changing anything.

| # | Check | Command / Action | Expected |
|---|-------|------------------|----------|
| 1.1 | 🔍 Clean git state | `git status --short` | Empty, or the changes under audit are the only ones. Never audit on top of unrelated uncommitted work |
| 1.2 | 🔍 Record the baseline suite | `.venv\Scripts\python.exe -m coverage run -m unittest discover -s tests -t .` | Records the test count and total coverage for before/after comparison |
| 1.3 | 🔍 Coverage floor | `python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['tool']['coverage']['report']['fail_under'])"` | Prints the floor (94). The coverage run exits non-zero below it |
| 1.4 | 🔍 Config present | Confirm `config/settings.json` and `config/contact.json` exist | Both gitignored; `settings.json` is required by `load_settings`, `contact.json` supplies identity |
| 1.5 | 🔍 Back up the database before touching it | `copy data\oilwatch.sqlite data\oilwatch-backup-<YYYYMMDD-HHMMSS>.sqlite` | A copy exists. Name it `*.sqlite` so `.gitignore` covers it and it cannot be committed |
| 1.6 | 🔍 Record the checkout root | `.venv\Scripts\python.exe -c "from oilwatch.config import CHECKOUT_ROOT; print(CHECKOUT_ROOT)"` | Prints the repository root, not the current directory |
| 1.7 | 🔍 Python version | `python --version` | 3.12 or newer (`requires-python = ">=3.12"`) |
| 1.8 | 🔍 No BOM in source | `grep -rlP '^\xEF\xBB\xBF' oilwatch/ tests/` | No matches — a BOM breaks the first import |
| 1.9 | 🔍 The checkers are installed | `.venv\Scripts\python.exe -m pip install -e ".[dev]"` | `ruff` and `pyright` are available; both are declared in the `dev` extra so a fresh checkout can run this checklist |
| 1.10 | 🔍 Record the lint/type baseline | `.venv\Scripts\python.exe -m ruff check . --statistics` and `.venv\Scripts\python.exe -m pyright` | Note the counts. Both tools' scope is pinned in the repo rather than inherited: `pyproject.toml` `[tool.ruff.lint]` selects the rules explicitly (with the reason beside each), and `pyrightconfig.json` pins `typeCheckingMode` to `standard` as well as including `oilwatch`, so bare `pyright` answers "is the *package* clean?" while `pyright tests` answers the other question. Pylance reads that same config file, so the pin is also what makes the editor agree with the command line — and it *overrides* any `python.analysis.typeCheckingMode` from VS Code, so setting that too only earns an ignored-setting warning (§15.19). A pass keeps the five recorded package artifacts at five (§8.9) and the test tree at zero, rather than aiming for a smaller backlog |
| 1.11 | 🔍 The subprocess coverage hook is present | `.venv\Scripts\python.exe -c "import pathlib;print(sorted(p.name for p in pathlib.Path('.venv/Lib/site-packages').glob('*.pth') if 'process_startup' in p.read_text(errors='replace')))"` | `['a1_coverage.pth']`. That file is coverage's documented startup recipe — it calls `coverage.process_startup()` only when `COVERAGE_PROCESS_START`/`COVERAGE_PROCESS_CONFIG` is set — and it lives in the *venv*, not the repo, so a fresh venv silently measures no subprocess at all: the MCP server is spawned as its own process. Pylance's `[Editable Installs] Failed to resolve pth package 'a1_coverage'` is its editable-install heuristic misreading the hook. Inspect it, decide, keep it — a `.pth` is code that runs at every interpreter start, which is why it deserves the look and not a mute |

## 2. Security — Critical

| # | Check | Command / Action | Expected |
|---|-------|------------------|----------|
| 2.1 | 🔧 No secret in a quote note | `grep -rn "notes=.*password" oilwatch/` | Zero matches. A note is a broadcast surface (§15.1) |
| 2.2 | 🔧 The registration note carries no credential | Read `browser_base._build_registration_instructions` | The password is never interpolated; the note names the store and a read-back command instead |
| 2.3 | 🔧 No hardcoded personal identity | `grep -rn "waynegault@msn.com\|Wayne Gault" oilwatch/` | Zero matches — guarded by `tests/test_identity.py` |
| 2.4 | 🔍 Credential store is encrypted at rest | `.venv\Scripts\python.exe -c "from oilwatch.credentials import CredentialManager as C; m=C(); print(m.config_path, m.is_encrypted, m.load_error)"` | `is_encrypted=True`, `load_error=None`. A non-Windows host writes plain text by design |
| 2.5 | 🔍 Credential files are ignored by git | `git check-ignore -v config/supplier_credentials.json config/contact.json config/settings.json data/oilwatch.sqlite data/oilwatch.log` | Each path is reported as ignored |
| 2.6 | 🔧 A password is never put in an error message | Read `browser_auth._set_field_value` and `_log_sign_in_failure` | Failures name the field and the URL, never the value |
| 2.7 | 🔧 Secrets are not logged | `grep -rn "log\..*password" oilwatch/` | Zero matches |
| 2.8 | 🔍 MCP tools carry annotations | `grep -n "@mcp.tool" oilwatch/mcp_server.py` | Every tool passes an `annotations=` from `_READ_ONLY` / `_LOCAL_ARTIFACT` / `_EXTERNAL_FETCH` / `_EXTERNAL_SLOW` |
| 2.9 | 🔧 Servers bind IPv4 only | `grep -n "MCP_HOST\|0.0.0.0\|::" oilwatch/mcp_server.py` | `127.0.0.1` default, `0.0.0.0` opt-in, no `::` anywhere |
| 2.10 | 🔧 No `eval`/`exec` on scraped content | `grep -rn "eval(\|exec(" oilwatch/` | Only `driver.execute_script` (Selenium) and `importlib.import_module`; no `eval` |
| 2.11 | 🔍 No accidental key-shaped literals | `grep -rnE "[A-Za-z0-9_-]{32,}" oilwatch/` | Every hit is a literal, not a credential. The pattern matches any long token, so on 2026-09-18 the fifteen hits were Chrome flags and CSS selectors (`--disable-features=AutofillServerCommunication,AutofillEnableAccountWalletStorage`, `#sgcPriceChecker_txtQuotePostcode`) plus the public Microsoft OAuth client id, which is already published in `monitor_email.bat`. The check is that none is a key — not that there are none — so read a hit before calling it benign |
| 2.12 | 🔧 No suppression of a security warning | `grep -rn "warnings.filterwarnings\|filterwarnings" oilwatch/ tests/` | Zero — warnings are signal |

## 3. Safety — Critical

OilWatch must never buy anything, and must not damage the host or the database.

| # | Check | Command / Action | Expected |
|---|-------|------------------|----------|
| 3.1 | 🔍 There is no ordering capability | `grep -rn "place_order\|place-order\|checkout" oilwatch/` | No order-placement path exists. Purchases are written by the owner through the CLI |
| 3.2 | 🔧 No destructive SQL | `grep -rniE "drop table\|delete from\|truncate" oilwatch/` | Zero (or each justified and scoped). The `quotes` table is append-only |
| 3.3 | 🔍 Schema changes are additive | Read `db.init_schema` | Migrations use `ALTER TABLE ... ADD COLUMN` and backfill, never a rewrite |
| 3.4 | 🔧 Browser automation is opt-in | `grep -n "prefer_browser" oilwatch/connectors/suppliers/__init__.py` | Default is HTTP; a browser connector runs only when asked |
| 3.5 | 🔍 Concurrency is capped | `grep -n "quote_max_workers" oilwatch/config.py oilwatch/service.py` | The pool is capped (default 4); no per-supplier rate limiting exists, so the cap is the guard |
| 3.6 | 🔧 A pre-filled form control is never cleared | `grep -rn "\.clear()" oilwatch/` | Each hit is either followed by a DOM assign with `input`/`change` events (`browser_auth._set_field_value`) or sits on a field whose value we own (`form_submit`). Clearing a site-prefilled control lets its validation rewrite the value (§15.4) |
| 3.7 | 🔍 DB writes happen in one thread | Read `service.quote_all` | Quotes are recorded after the pool, in the calling thread, so concurrent workers cannot contend for the file |
| 3.8 | 🔍 Backups are gitignored | Confirm any `*.sqlite.bak*` was renamed to `*.sqlite` | The ignore rule is `*.sqlite`, so a `.bak` suffix is NOT ignored and could be committed |
| 3.9 | 🔍 Nothing is scheduled at logon | `dir /b "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"` (write the literal path if the shell rejects `%`) | No live OilWatch entry: prices refresh on request, by design |

## 4. Correctness — High

| # | Check | Command / Action | Expected |
|---|-------|------------------|----------|
| 4.1 | 🔧 Prices are normalised in one place | `grep -rn "price_per_liter" oilwatch/pricing.py` | Every connector's price goes through `pricing.py` |
| 4.2 | 🔧 VAT is the 5% domestic rate | Read `oilwatch/pricing.py` | `VAT_RATE = 0.05`; the stored price is inclusive |
| 4.3 | 🔍 `total_price` agrees with the rate | Inspect a freshly recorded row | `total_price == price_per_liter × quantity_liters`, or the site's own stated total with `quantity_liters` set to what the site quoted |
| 4.4 | 🔍 A quote states its window | `.venv\Scripts\python.exe -m oilwatch.cli cheapest` | Rows carry `observed_at` and `valid_until` (default 24 h from observation) |
| 4.5 | 🔧 Staleness is applied on read | `grep -n "max_quote_age_days" oilwatch/service.py` | `latest_quotes` filters by the window; `excluded_suppliers` and `not_refreshed_suppliers` name what it held back |
| 4.6 | 🔧 The benchmark cannot win | `grep -n "BENCHMARK_SOURCES" oilwatch/analytics.py` | Fueltool is held out of the ranking, the average and the variance, and reported as `benchmark` |
| 4.7 | 🔍 The trend is per-supplier, not per-minimum | Read `analytics.price_trend` | The median change across suppliers quoting on both days; the daily minimum alone must not flip it |
| 4.8 | 🔍 Pence converts to pounds | `grep -n "pence_to_pounds" oilwatch/pricing.py` | A `NN.NNp` value becomes £0.NN |
| 4.9 | 🔍 Statuses are distinct | Read a connector's return paths | `ok` only with a real price; `manual_action_required` when the site gives none; `error` on a fault. A missing price is never fabricated |
| 4.10 | 🔧 The site's own figures are reported | Inspect `scottish_fuels_browser.parse_quote_row` | The quantity, rate and inclusive total come from the site's row, not from multiplying what we asked for (§15.5) |

## 5. Robustness — High

| # | Check | Command / Action | Expected |
|---|-------|------------------|----------|
| 5.1 | 🔧 One supplier cannot abort a sweep | Read `service.quote_all` | Each supplier is quoted in a guarded call; a failure becomes an `error` row and a warning |
| 5.2 | 🔍 Page loads are bounded | `grep -n "PAGE_LOAD_TIMEOUT_S" oilwatch/browser_auth.py` | A stalled third-party script fails the step rather than blocking the run |
| 5.3 | 🔧 Waits are signal-based | `grep -rn "wait_until\|_wait_for" oilwatch/connectors/` | Readiness is polled against a real signal; a flat `sleep` is not the primary wait |
| 5.4 | 🔍 Optional login never blocks a quote | Read `browser_base._optional_login` | Returns `True` regardless, and logs the failure so it is visible |
| 5.5 | 🔧 A failure says what it saw | `grep -rn "text follows\|body (captured)\|page text follows" oilwatch/` | The page text or response body is logged on a parse failure — fix against that, not against a guess |
| 5.6 | 🔍 HTTP calls have timeouts | `grep -rn "timeout" oilwatch/http.py oilwatch/connectors/suppliers/*.py` | Every outbound request carries one |
| 5.7 | 🔍 A submit is verified, not assumed | Read `browser_auth._submit_sign_in` | The page's own navigation is required as proof; a click that raises nothing is not success |
| 5.8 | 🔍 Failures are surfaced | `grep -n "notify_errors" oilwatch/service.py` | A failed sweep raises a toast rather than only writing a log line |
| 5.9 | 🔧 No silent swallow | `grep -rn -A2 "except Exception" oilwatch/` | Every broad handler logs or returns a described result; no bare `pass` |

## 6. Efficiency & Concurrency — Medium

| # | Check | Command / Action | Expected |
|---|-------|------------------|----------|
| 6.1 | 🔍 Connectors import lazily | Read `connectors/suppliers/__init__.py` | Module-level `__getattr__` (PEP 562) imports only the connector selected, so an HTTP-only run never loads Playwright |
| 6.2 | 🔍 The pool is bounded | `grep -n "ThreadPoolExecutor" oilwatch/service.py` | `max_workers=min(workers, len(suppliers))` |
| 6.3 | 🔍 Charts release their figures | `grep -n "plt.close" oilwatch/analytics.py` | Every chart closes its figure |
| 6.4 | 🔍 Request interception does not stall a site | `grep -n "_intercept_requests" oilwatch/connectors/browser_base.py` | A connector whose site stalls when proxied can opt out |
| 6.5 | 🔍 Temp files are removed | `dir /b .qwen\tmp` | No leftovers *from this pass*; intermediates are deleted when done. `.qwen/` is the owner's scratch and is out of audit scope, so a file that predates the pass is reported rather than cleared — on 2026-09-18 `store_boilerjuice.py`, `email-task.backup.xml` and `msg16.txt` all predated it and were left alone (`store_boilerjuice.py` was scanned for `password = "..."`-shaped assignments and has none) |

## 7. Configuration & Path Resolution — High

This is where the 2026-09-15 defects lived: everything resolved from the process
working directory, but a spawned MCP server does not inherit the repo as its cwd.

| # | Check | Command / Action | Expected |
|---|-------|------------------|----------|
| 7.1 | 🔧 No working-directory default in the package | `grep -rn "Path.cwd()" oilwatch/` | Zero matches |
| 7.2 | 🔧 One definition of the checkout | `grep -rn "parents\[1\]" oilwatch/` | `config.CHECKOUT_ROOT` is the single definition (plus `mcp_server.ROOT`, kept for its explicit-root contract) |
| 7.3 | 🔍 Config, credentials, identity and DB agree | Read `config.load_settings`, `identity._resolve`, `credentials.CredentialManager`, `service.OilWatchApp` | All four default to `CHECKOUT_ROOT` |
| 7.4 | 🔍 Resolution is cwd-independent | `.venv\Scripts\python.exe -c "import os;from oilwatch.identity import load_contact;os.chdir(os.environ.get('TEMP'));print(load_contact(refresh=True))"` | Prints the configured contact, not an empty one |
| 7.5 | 🔍 Environment wins over files | Read `identity._resolve` | Order is `OILWATCH_*` env → `contact.json` → `settings.json` |
| 7.6 | 🔍 Example and live settings agree on keys | `.venv\Scripts\python.exe -m unittest tests.test_docs.SettingsDriftTests` | Passes (skipped where `settings.json` is absent) |
| 7.7 | 🔍 The log path is defined once | `grep -n "OILWATCH_LOG_FILE" oilwatch_env.bat start_scheduler.bat monitor_email.bat` | Defined in `oilwatch_env.bat` only; a launcher calls that file |
| 7.8 | 🔧 No `/mnt/c/...` path as an argument | `grep -rn "mnt/c" oilwatch/` | None — WSL translates the executable path but not argument paths |
| 7.9 | 🔍 The space-in-path trap is documented | `grep -n "Projects/Oil ENOENT" user-guide.md` | Present: a client that word-splits the stdio command breaks on `Oil Price Webscraper` |

## 8. Style & Conventions — Medium

| # | Check | Command / Action | Expected |
|---|-------|------------------|----------|
| 8.1 | 🔧 Warnings are never suppressed | `grep -rn "assertLogs\|filterwarnings" tests/` | A test may assert a warning; it must not silence one |
| 8.2 | 🔧 Comments explain why | Spot-read the changed hunks | A comment states the reason (a past incident, a hidden constraint), never a restatement of the code |
| 8.3 | 🔍 Module docstrings present | `head -3 oilwatch/*.py` | Every module opens with a purpose statement |
| 8.4 | 🔧 Suppressions are justified | `grep -rn "noqa" oilwatch/` | Each carries a rule code and a reason; a blanket suppression is not a fix |
| 8.5 | 🔍 No trailing whitespace in changed files | `grep -rnP " +$" <changed files>` | Clean |
| 8.6 | 🔍 Type hints on public functions | Spot-read `oilwatch/service.py` | Requests and returns annotated; `from __future__ import annotations` at the top of each module |
| 8.7 | 🔧 Ruff is clean on the files you touched | `.venv\Scripts\python.exe -m ruff check <changed files>` | Zero findings. Which rules count is pinned in `pyproject.toml`, not inherited from Ruff's defaults — those widened between releases. A violation is fixed, or silenced with `# noqa: <rule> - <reason>`; never by widening the ignore list |
| 8.8 | 🔧 Pyright is clean on the files you touched | `.venv\Scripts\python.exe -m pyright <changed files>` | Zero errors on those files. Pylance *is* Pyright — it bundles the same engine (`1.1.414` here, matching the venv's) — but it has its own default analysis mode (`off`), so the mode is pinned in `pyrightconfig.json`, the one file both readers honour: Pylance overrides any `python.analysis.typeCheckingMode` from VS Code with it, and logs the VS Code setting as ignored if both are set (§15.19). The config also includes `oilwatch`, so bare `pyright` reports the package while `pyright tests` reports the tree. Chase the files you touched, not the tree's backlog |
| 8.9 | 🔍 The five package errors that remain are accepted artifacts, and the test tree is clean | `.venv\Scripts\python.exe -m pyright` and `.venv\Scripts\python.exe -m pyright tests` | Bare `pyright` reports five errors and one warning: `analytics.py` ×4 (matplotlib's `plot`/`legend` do not accept `list[datetime]`/`list[object]`) and `import_xls.py` ×1 (xlrd wants `Literal[0, 1]` for `datemode`) — neither is fixable without casting working code, so they are recorded rather than chased. The warning is pyright's `reportUnsupportedDunderAll` on the `*_CONNECTORS` unpacking in §8.7. `pyright tests` reports zero, so *any* test-tree error is new work: the fakes now satisfy protocols the real classes also satisfy (§15.15, §15.16). `tools/` sits deliberately outside that configured scope (`pyrightconfig.json` `include` is `["oilwatch"]`, the package), so `pyright tools` is a command to run rather than part of this baseline — it reports zero, and the explorer builder is linted by `ruff check .` like every other file |

## 9. Documentation & Guards — Medium

| # | Check | Command / Action | Expected |
|---|-------|------------------|----------|
| 9.1 | 🔍 Documented counts match the code | `.venv\Scripts\python.exe -m unittest tests.test_docs -v` | Passes. `test_docs.py` reads the MCP tool count, the CLI subcommand count, the test count and the module count back out of the code, and checks that every tool and every command is named in the reference (§9.8) |
| 9.2 | 🔧 Counts are updated when code changes | Add a tool, a command or a test | Update `PROGRESS.md` and `ROADMAP.md` in the same commit, or the suite fails |
| 9.3 | 🔍 The README index matches the files present | Compare the README's file tree and Support & Documentation table with `dir /b *.md` | Every listed document exists; no deleted document is still listed |
| 9.4 | 🔧 No prices in the guide or README | `grep -rnE "£1\.[0-9]{3}" README.md user-guide.md` | No hardcoded prices — they rot within weeks; link to `oilwatch cheapest` instead. `PROGRESS.md` and `ROADMAP.md` are exempt: they are a dated record, and a price there is history, not a claim about today |
| 9.5 | 🔍 The agent brief is current | `grep -n "openclaw mcp probe" user-guide.md` | The brief (user-guide.md §14) states the tool count and the refresh cost |
| 9.6 | 🔍 Connector documentation matches the registries | Compare `user-guide.md` §9's domain table with `_SUPPLIER_CONNECTORS` | The same domains, the same connector names |
| 9.7 | 🔍 Every document ends with a newline | `tail -c 1 README.md` | Ends in `0a` |
| 9.8 | 🔍 Every command and tool is named in the reference | `.venv\Scripts\python.exe -m unittest tests.test_docs.ReferenceTests` | Passes. README is the tool reference `AGENTS.md` sends an agent to, so it must name all 21 CLI subcommands (in a command position, so the prose word "quote" cannot stand in for the `quote` command) and all 9 MCP tools. On 2026-09-18 five commands were missing from it — `import-spreadsheet`, `login-email`, `submit-requests`, `time-series`, `update-brent` — so the check reads both lists back out of the code rather than trusting the prose |

## 10. Testing — High

| # | Check | Command / Action | Expected |
|---|-------|------------------|----------|
| 10.1 | 🔧 The suite is offline | `grep -rn "requests.get\|httpx.get" tests/` | HTTP is mocked and SQLite is a temp database — no network, no supplier site contacted |
| 10.2 | 🔍 Coverage at or above the floor | `.venv\Scripts\python.exe -m coverage report -m` | Total ≥ 94 (measured 96.0 on 2026-09-18; it was 95.5 when this item was written). A line ending in an ellipsis is excluded as a declaration, not behaviour (§15.17) |
| 10.3 | 🔧 Tests pin contracts, not coverage | Read the tests added by a change | Each pins a behaviour a change could break — not a restatement of a one-line delegation |
| 10.4 | 🔧 A fake matches the real library | Read the fake for a changed connector | It accepts the real signature and models the real value (a control's pre-filled value, the DOM set actually used) — a fake that returns `None` for every lookup makes a path silently untested. Where the faked type is a third party's, the protocol in `oilwatch/connectors/protocols.py` — or in `geo.py`/`discovery.py` — is what `pyright tests` holds the fake to (§15.16) |
| 10.5 | 🔧 A test that pinned wrong behaviour is inverted | Search the suite for the old assertion | When a defect is fixed, the test asserting the defect is rewritten, not deleted |
| 10.6 | 🔧 New behaviour has a regression test | Diff the test files | Every fix in the change carries a test that fails against the old code |
| 10.7 | 🔍 The doc-guard tests pass | `.venv\Scripts\python.exe -m unittest tests.test_docs` | Passes; the counts were updated with the change |
| 10.8 | 🔧 A lint auto-fix is verified, not trusted | `.venv\Scripts\python.exe -m ruff check . --select F401 --fix` then the full suite | `--fix` infers intent from syntax alone. Removing an "unused" import turned two green tests red: the import was the patch anchor (`price_page.httpx` **is** the httpx module, so the tests reach `httpx` through it). Always re-run the suite after an auto-fix |
| 10.9 | 🔧 The coverage exclusion is asserted, not assumed | `.venv\Scripts\python.exe -m unittest tests.test_coverage_exclusions` | Passes. `exclude_lines` skips every line ending in an ellipsis, so the suite pins the precondition it rests on: every such statement in the package is a Protocol member body, and the configured pattern still matches those lines. The first says the exclusion is *safe*, the second that it is *in effect* (§15.17) |

## 11. MCP & AI Agent Access — High

| # | Check | Command / Action | Expected |
|---|-------|------------------|----------|
| 11.1 | 🔍 Tool count | `.venv\Scripts\python.exe -m unittest tests.test_docs.CountTests.test_the_mcp_tool_count_matches_the_server` | Nine |
| 11.2 | 🔍 A real handshake lists them | Spawn `python -m oilwatch.mcp_server --stdio` with an MCP client and call `list_tools` | Nine tools; `chart, cheapest, current_prices, list_suppliers, purchases, refresh_prices, status, time_series_chart, update_brent` |
| 11.3 | 🔍 A read call succeeds | Call `cheapest` over that session | `isError` is false |
| 11.4 | 🔍 No write/order tool exists | Review the tool list | `purchases` reads only; nothing places an order |
| 11.5 | 🔍 Slow tools say so | Read `refresh_prices`'s docstring | States minutes, per-supplier failure, and "do not call it in a loop" |
| 11.6 | 🔧 Client-side traps documented | `grep -n "space-free wrapper" user-guide.md` | The space-in-path and `/mnt/c` traps are in the brief |

## 12. Data & Storage — Medium

| # | Check | Command / Action | Expected |
|---|-------|------------------|----------|
| 12.1 | 🔍 The DB path comes from settings | `grep -n "database_path" config/settings.json oilwatch/config.py` | Read from `settings.json`, resolved under the checkout |
| 12.2 | 🔧 No secret in a stored row | Query `quotes.notes` for credential-shaped text | No row carries a password. Check after any change to a connector's notes |
| 12.3 | 🔍 History is preserved, not deleted | `grep -n "def all_quotes\|def latest_quotes" oilwatch/db.py` | Reads filter by window; rows are not removed |
| 12.4 | 🔍 Migrations backfill | Read `db.init_schema` | New columns are added and existing rows backfilled (e.g. `valid_until` = observed + 24 h) |
| 12.5 | 🔍 Generated files stay out of git | `git status --short data/` | Nothing reported — `*.sqlite`, `*.log` and `*.png` are ignored |

## 13. Supplier Connector Accuracy — High

A connector that returns a plausible wrong number is worse than one that returns
nothing. These are the checks that catch it.

| # | Check | Command / Action | Expected |
|---|-------|------------------|----------|
| 13.1 | 🔧 Every required input is actually set | Read the connector's form fill | The postcode, quantity and any required field are set, and the *value that stuck* is verified — an empty postcode is what silently produced "no offer" |
| 13.2 | 🔧 The quantity is the quantity ordered | Run the supplier the live way and read the note | The note's quantity matches the site's row, not the value requested |
| 13.3 | 🔧 The reported total is the site's total | Compare `total_price` with the site's own row | Equal, or derived from the site's own numbers |
| 13.4 | 🔧 The parse is written against real text | `grep -n "text follows" oilwatch/connectors/suppliers/<supplier>.py` | The connector logs the page/response it could not parse; the pattern was written from that capture |
| 13.5 | 🔍 Status reflects what happened | Run the supplier once | `ok` only with a price read off the page; the site quoting a different quantity than requested is stated, not relabelled |
| 13.6 | 🔍 The benchmark is never the winner | `.venv\Scripts\python.exe -m oilwatch.cli cheapest` | Fueltool appears under `benchmark`, never as `cheapest_supplier` |
| 13.7 | 🔍 A live spot-check agrees with the site | Quote one supplier and open its page | The figure matches; discrepancies are connector bugs, not rounding |
| 13.8 | 🔍 Registry and connector agree | `grep -n "_SUPPLIER_CONNECTORS" -A 20 oilwatch/connectors/suppliers/__init__.py` | Each domain maps to a class that exists in `_CONNECTORS`; `tests/test_connector_imports.py` covers it |

## 14. Final Validation

| # | Check | Command / Action | Expected |
|---|-------|------------------|----------|
| 14.1 | 🔍 The suite is green at the floor | `.venv\Scripts\python.exe -m coverage run -m unittest discover -s tests -t . && .venv\Scripts\python.exe -m coverage report -m` | All tests pass; total ≥ 94 |
| 14.2 | 🔍 The CLI runs against the live database | `.venv\Scripts\python.exe -m oilwatch.cli status` | A market snapshot with a cheapest supplier, a trend and a recommendation |
| 14.3 | 🔍 The MCP server handshakes | Spawn `--stdio` and list tools | Nine tools, and a read call returns without error |
| 14.4 | 🔍 One supplier quoted end to end | `.venv\Scripts\python.exe -m oilwatch.cli quote <id> --browser` | A price with `observed_at` and a note naming the quantity and basis |
| 14.5 | 🔍 No leftovers | `dir /b .qwen\tmp`; `git status --short` | No probe scripts or capture logs from this pass, and only intended changes in git. Pre-existing scratch is the owner's: name it, do not clear it (§6.5) |
| 14.6 | 🔍 Findings recorded | Review the audit notes | Each finding has a severity, a location and a remediation |

## 15. New Insights & Standards (2026-09-15)

Hard-won rules from a real audit pass. Each is a check, not an anecdote.

| # | Check | Command / Action | Expected |
|---|-------|------------------|----------|
| 15.1 | 🔧 A broadcast surface never carries a secret | `grep -rn "notes=" oilwatch/connectors/` | No credential in a note. A note reaches the MCP caller, the `quotes` table **and** the log at once — the 2026-09-15 leak put a password in all three |
| 15.2 | 🔧 Never resolve config from the process cwd | `grep -rn "Path.cwd()" oilwatch/` | Zero. The MCP server is spawned by another program and does not inherit the repo as cwd; a cwd default emptied the postcode and the credential lookup |
| 15.3 | 🔧 Check for an empty input before blaming the site | Reproduce a failure with the real values | "no offer in the response" and "no stored credentials" were both an empty postcode/credential, not a supplier fault. Verify the input reached the request |
| 15.4 | 🔧 Never clear a pre-filled form control | `grep -rn "\.clear()" oilwatch/connectors/` | Zero on a pre-filled control. Clearing the Scottish Fuels quantity let the page rewrite it to its 500 L minimum, and every 1000 L quote became a 500 L quote |
| 15.5 | 🔧 Report the site's own figures | Read the connector's result construction | The site's quantity, rate and total — not arithmetic on the value we requested |
| 15.6 | 🔧 Silence is not evidence of failure | `grep -rn "did not take" oilwatch/browser_auth.py` | A success path logs its success. Only the failure paths logging made a *successful* sign-in read as a broken one |
| 15.7 | 🔧 Invert any test that pinned a defect | Search the suite for the fixed behaviour | The test asserting the leak, the cwd default and the `send_keys` call were each rewritten when the behaviour was fixed, not deleted |
| 15.8 | 🔧 Update guarded counts with the change | `.venv\Scripts\python.exe -m unittest tests.test_docs` | Passes. Adding a test or a tool without updating `PROGRESS.md`/`ROADMAP.md` fails the suite |
| 15.9 | 🔧 Keep the repo path free of surprises for clients | `grep -n "Oil Price Webscraper" user-guide.md` | The space trap is documented, because a client that word-splits the stdio command cannot use the raw path |
| 15.10 | 🔍 Read the recorded failure before changing code | `.venv\Scripts\python.exe -m oilwatch.cli status` | The `last_attempt_note` names what each connector actually got. Six "broken" suppliers were one input defect, not six connector bugs |
| 15.11 | 🔧 An "unused" import may be a patch anchor | `grep -rn "<module>\.<name>" tests/` before deleting it | An import can be unused in the source and still load-bearing: a test that patches `thatmodule.name` needs the name to exist there. `F401` cannot see that, and removing `price_page.httpx` broke two tests. If it is only an anchor, fix the *test* to patch the module that really uses it |
| 15.12 | 🔧 A fake that never raises hides the handler it feeds | Grep the fake for the method the code guards | `_set_field_value` caught `WebDriverException` without importing it — a `NameError` inside the handler — and no test ever reached it because the fake's `clear()` could not raise. Make the fake raise, and the branch is both covered and pinned |
| 15.13 | 🔧 Suppress once, at the boundary | Inspect assignments of a fake to a real-typed attribute | Six assignments of a fake driver to `BrowserAuth.driver` became one helper with one owned `# type: ignore[assignment]`, and a fake whose type is annotated as the real class is narrowed with one `cast(...)` at the call. A suppression repeated per site hides which of them is the real exception |
| 15.14 | 🔧 Verify with the tool, never by reasoning | Run the checker that reported the finding, on the files you touched | A diagnostic fixed by inspection cannot be claimed gone. Install the tool (`.venv\Scripts\python.exe -m pip install -e ".[dev]"`) and re-run it: Pylance is Pyright, and with the analysis mode pinned on both sides (§8.8) `pyright` reproduces the editor exactly |
| 15.15 | 🔧 A generated third-party API needs the interface declared, not imported | Run the checker on the file that calls it | geopy builds `geocode` behind a decorator that switches on the adapter, so a synchronous call read as a coroutine and its result as unknown — four "accepted artifacts" in `geo.py`. Naming the two members we use (`GeocoderLike`, `LocationLike`) and casting once where the real geocoder is created is what made those four go away; the call site became checked instead of excused |
| 15.16 | 🔧 A protocol for a third party's type; a cast for our own class | Find the fake, then ask who owns the type it stands in for | `PageLike`/`ElementLike`/`ClientLike`/`GeoServiceLike` let fakes satisfy a *narrow* interface the real class already meets, and the source stays checked against it. For `OilWatchApp`, whose stub is a convenience for the scheduler, an eight-member protocol would have been a hand-maintained copy of our own API and would have stopped checking the scheduler against the app it drives — one cast in one test helper was the better trade (§15.13). Decide by what the source would stop checking, not by symmetry |
| 15.17 | 🔧 Check what a stub branch really measures before lowering a floor | `.venv\Scripts\python.exe -m coverage report -m` after adding a protocol | Adding two protocol modules moved the total from 95.45 to 94.75 without a line of behaviour changing: a stub body can never be entered, so its entry branch counts as partial for good, and the drag grows with every protocol. An ellipsis body is a declaration — `exclude_lines` now says so, and the figure is 95.52. Do not reach for `omit` on the file: that hides real statements if the file ever gains any. `tests/test_coverage_exclusions.py` asserts the precondition that makes the exclusion sound (§10.9) |
| 15.18 | 🔍 A default is not a decision — read the tool's own default before pinning it | Guessing the mode from two probe rules, then checking the bundled default | Two rules I *believed* were standard-level were strict-only, so probes that came back clean "proved" the CLI was at `basic`; pyright's own bundled default object says `typeCheckingMode: "standard"`, and pinning `basic` would have hidden diagnostics that the recorded count includes. Pin what the tool actually resolves to, and confirm the pin changes nothing (`pyright` still reported the same five) |
| 15.19 | 🔧 A setting the config file overrides is not a mirror, it is a warning | Pin something in both the tool's config and the editor's settings, then read the language-server log | Pinning `python.analysis.typeCheckingMode` beside `pyrightconfig.json`'s `typeCheckingMode` logged "are being overridden by pyrightconfig.json and will be ignored: python.analysis.typeCheckingMode". The config file is what the CLI *and* Pylance honour, so pin the mode there once; the duplicate was dead weight that announces itself as ignored. Check the language-server log after any editor-settings change — Pylance reports what it ignored, which is not visible anywhere else |

<!-- end of file -->
