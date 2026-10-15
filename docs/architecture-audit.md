# Architecture audit findings

This is the tracked findings register for the 2026-10-15 architecture audit.
Items are ordered by risk and evidence, not by the order in which they are
addressed. Findings are closed only after a targeted test and a separate commit.

## Findings

| ID | Priority | Finding | Evidence | Disposition |
| --- | --- | --- | --- | --- |
| AUD-001 | P1 | Configuration and core quote models do not use Pydantic validation. JSON is decoded into untyped dictionaries, then copied into dataclasses; malformed values and unknown keys can travel into runtime and persistence. | `oilwatch/config.py:38-145`, `oilwatch/models.py:61-160`; `pyproject.toml` has no direct Pydantic dependency. | Open — add strict boundary models and invalid-input tests. |
| AUD-002 | P1 | The detached quote worker inherits `sys.executable`, so a caller started outside the checkout's `.venv` launches a worker outside it too. A subprocess failure before application startup is also hidden because both output streams are discarded. | `oilwatch/service.py:584-612`; repository launchers use `.venv`, but this worker does not. | Open — require and launch the checkout venv; preserve a diagnostic path for early worker failures. |
| AUD-003 | P1 | Several unit-test fixtures contain the owner's identifying contact and home-location values instead of synthetic data. A static test checks only `oilwatch/`, not the test tree, so it does not guard this exposure. | `tests/test_email_monitor.py:267-273`, `tests/app_fixture.py:27`, `tests/test_app.py:25,272`, `tests/test_identity.py:99-113`. | Open — replace with synthetic values and extend the guard to test fixtures. |
| AUD-004 | P2 | A malformed newest-observation timestamp is treated as if no recent sweep existed. The caller may start another costly browser sweep, while the invalid database value is not logged or surfaced. | `oilwatch/service.py:671-689`; malformed timestamps elsewhere in the same service are not handled the same way. | Open — make corrupt stored state explicit and test it. |
| AUD-005 | P2 | The service-test setup is duplicated: `tests/test_app.py` writes its own temporary config/database while `tests/app_fixture.py` already owns that shared scenario. This makes configuration drift and inconsistent test-data hygiene likely. | `tests/test_app.py:16-35,264-282`; `tests/app_fixture.py:51-82`. | Open — consolidate on the shared fixture without importing its supplier defaults into tests that expect an empty register. |
| AUD-006 | P2 | The two largest package modules (`db.py`, 1,038 lines; `service.py`, 836 lines) warrant function-level review for cohesive boundaries. File size alone is not proof that splitting improves the design. | File inventory at audit start. | Investigating — split only where an existing responsibility boundary and tests support it; otherwise document why no extraction is warranted. |
| AUD-007 | P1 | Five effective-price integration tests use a fixed 2026-09-10 quote even though the default comparison window is 30 days. The fixture ages out and those tests fail as time passes. | `tests/test_effective_price.py:22-24,84-102`; baseline run on 2026-10-15 had 5 errors from this cause. | Open — make observations relative to the test clock and assert the window contract. |
| AUD-008 | P1 | The baseline unit suite is not hermetic: six CLI tests and the default-root test need the ignored local settings file; three sync-connector tests launch a real Playwright browser despite testing stub results. | `tests/test_cli.py` direct `main()` calls; `tests/test_app.py:259-291`; `tests/test_connector_bases.py:29-77`. Baseline: 837 tests, 3 failures, 12 errors, 6 skipped, 612.8 seconds; settings file and Playwright browser were absent. | Open — isolate the tests with synthetic config and mocked browser launch, then rerun. |

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

## Commit log

| Batch | Commit | Scope |
| --- | --- | --- |
| Findings register | Pending | Initial evidence and prioritized audit queue |
