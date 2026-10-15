# Architecture audit findings

This is the tracked findings register for the 2026-10-15 architecture audit.
Items are ordered by risk and evidence, not by the order in which they are
addressed. Findings are closed only after a targeted test and a separate commit.

## Findings

| ID | Priority | Finding | Evidence | Disposition |
| --- | --- | --- | --- | --- |
| AUD-001 | P1 | Configuration and core quote models did not use Pydantic validation. JSON was decoded into untyped dictionaries, then copied into dataclasses; malformed values and unknown keys could travel into runtime and persistence. | `oilwatch/config.py`, `oilwatch/models.py`; Pydantic was only transitive before the refactor. | Fixed in this batch — strict settings, supplier-register, candidate, and quote models reject unknown fields and invalid types; quote status/price/reason combinations are validated. Connector-specific nested `connector_config` payloads remain an explicit follow-up. |
| AUD-002 | P1 | The detached quote worker inherited `sys.executable`, so a caller started outside the checkout's `.venv` launched a worker outside it too. A subprocess failure before application startup was hidden because both output streams were discarded. | `oilwatch/service.py:start_background_sweep`; the worker spawned via `subprocess.Popen`. | Fixed in this batch — require the checkout `.venv` interpreter, retain worker stderr in ignored `data/oilwatch-worker.log`, and mark spawn failures as failed jobs before re-raising. |
| AUD-003 | P1 | Several unit-test fixtures contained identifying contact, home-location, and real discount-code values rather than synthetic data. The static guard checked only `oilwatch/`, not the test tree. | `tests/test_email_monitor.py`, `tests/app_fixture.py`, registration/form fixtures, and discount parsing tests. | Fixed in this batch — replace owner-specific fixture values and discount codes, and extend the guard to scan `oilwatch/` and tests (excluding the guard's own needle definitions). |
| AUD-004 | P2 | A malformed newest-observation timestamp was treated as if no recent sweep existed. The caller could start another costly browser sweep, while the invalid database value was not logged or surfaced. | `oilwatch/service.py:refresh_recently_done`; the method fed the MCP refresh cooldown. | Fixed in this batch — log and raise for invalid or timezone-aware stored timestamps rather than treating them as absent. |
| AUD-005 | P2 | The service-test setup was duplicated: `tests/test_app.py` wrote its own temporary config/database while `tests/app_fixture.py` already owned that shared scenario. | `tests/test_app.py`; `tests/app_fixture.py`. | Fixed in this batch — app, effective-price, and service-flow tests now use the shared temporary-root fixture with an explicit empty supplier register where required. |
| AUD-006 | P2 | The two largest package modules (`db.py`, 1,038 lines; `service.py`, 836 lines) warrant function-level review for cohesive boundaries. File size alone is not proof that splitting improves the design. | File inventory at audit start; 41 database methods and 34 application-facade methods reviewed by responsibility. | Reviewed — no module extraction justified: `Database` methods share SQLite schema/connection and row-mapping contracts; `OilWatchApp` is the application facade and its operations coordinate shared collaborators. Keep the size visible for a future change that establishes an independent persistence or application-service boundary. |
| AUD-007 | P1 | Five effective-price integration tests used a fixed 2026-09-10 quote even though the default comparison window is 30 days. The fixture aged out and the tests failed as time passed. | `tests/test_effective_price.py`; baseline run on 2026-10-15 had failures/errors from this cause. | Fixed in this batch — quote timestamps and validity are generated relative to the test clock; discount observations remain synthetic and recent. |
| AUD-008 | P1 | The baseline unit suite was not hermetic: CLI dispatch and default-root tests needed the ignored local settings file; three sync-connector tests launched a real Playwright browser despite testing stub results. | `tests/test_cli.py`, `tests/test_app.py`, `tests/test_connector_bases.py`. Baseline: 837 tests, 3 failures, 12 errors, 6 skipped, 612.8 seconds; settings file and Playwright browser were absent. | Fixed in this batch — CLI tests use a configured mock app, default-root checks use a temporary checkout, and browser tests stub page creation. |
| AUD-009 | P1 | The fuel-mail classifier reads its API key but sends a literal mask as the `Authorization` header. Any real request is unauthorized; the existing test asserted the broken mask rather than the bearer-token contract. | `oilwatch/quote_judge.py:242`; `tests/test_quote_judge.py` asserted the mask. | Fixed in this batch — the request now sends the configured bearer key and the mock verifies it. |
| AUD-010 | P2 | The LLM-using mail-classification path has request-shape/failure tests and a threshold calibration utility, but no fixed, labeled benchmark suite that reports classification quality, latency, and cost across model changes. The current test suite cannot detect quality or performance regressions. | `oilwatch/quote_judge.py`, `tools/fuel_mail_calibration.py`, `tests/test_quote_judge.py`; no benchmark dataset or benchmark runner is present. | Deferred — requires an owner-reviewed, sanitized labeled corpus and a chosen metric; do not invent labels or call the external model from unit tests. |
| AUD-011 | P2 | The repository-wide type check reported 15 errors and one warning: quote reason/status strings were wider than their validated vocabularies, chart date inputs and legend labels did not satisfy Matplotlib's typed API, the Excel date mode was unchecked, and lazy exports were not statically declared. | Full Pyright run on 2026-10-15; affected code in `analytics.py`, connector bases, `import_xls.py`, and `connectors/suppliers/__init__.py`. | Fixed in this batch — propagated closed quote literal types, converted date inputs through Matplotlib's date-number API, validated Excel date mode, and declared lazy exports for type checking without eager imports. Full Pyright and focused tests pass. |

## Audit coverage and exclusions

- Baseline test execution is being recorded before implementation. Each
  refactoring batch will use the checkout's `.venv` Python, run focused tests,
  and be committed independently.
- Baseline command: `.venv\Scripts\python.exe -m unittest discover -s tests -t .`
  (837 tests; 3 failures, 12 errors, 6 skips). The failures are pre-existing
  environment/test-fixture issues listed in AUD-007 and AUD-008; no repository
  files were changed before this run.
- The checkout did not contain `.venv`; it has been bootstrapped with Python
  3.12 and the project's declared development extra.
- Initial inspection found existing safeguards for generated artifacts,
  coverage exclusions, command/tool reference drift, and connector error
  reporting (`docs/inspection.md`, `tests/test_snapshot.py`,
  `tests/test_coverage_exclusions.py`, `tests/test_docs.py`). These are not
  counted as findings absent a failing check.
- No supplier refresh, mailbox access, quote request, or purchase action is part
  of this audit.
- The final full-suite run after the fixture/isolation batch passed 844 tests
  with 6 skipped. Project-wide Ruff and Pyright are clean after the typing batch.

## Commit log

| Batch | Commit | Scope |
| --- | --- | --- |
| Findings register | `b3e2661` | Initial evidence and prioritized audit queue |
| LLM authorization | `e8d8f66` | Use the configured bearer credential in the classifier request |
| Strict Pydantic boundaries | `4839def` | Validate settings, supplier register, candidates, and quote results; make service fixtures provide a valid synthetic register |
| Detached-worker integrity | `3ebb736` | Use the checkout virtualenv, retain worker stderr, mark launch failures, and reject malformed observation timestamps |
| Hermetic and synthetic test data | `b1f9f23` | Consolidate app fixtures; replace identifying data and stale timestamps; isolate CLI and browser tests |
| Project-wide typing contracts | `6e0360a` | Close quote status/reason types, plotting date/label types, Excel date-mode validation, and lazy-export type declarations |
