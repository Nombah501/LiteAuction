# Bug Triage Policy

## Severity

- `P0` critical: data corruption, security break, or core flow blocked for all users.
- `P1` high: major flow broken or incorrect behavior with significant impact.
- `P2` medium: partial degradation, UX break, or edge-case logic issue.
- `P3` low: minor cosmetic or low-impact inconsistency.

## Required Bug Card Fields

- Title
- Severity (`P0`/`P1`/`P2`/`P3`)
- Impacted area (`bot`, `web`, `timeline`, `rbac`, `moderation`, `infra`)
- Environment (`local`, `ci`, `prod-like`)
- Reproduction steps (deterministic, numbered)
- Expected result
- Actual result
- Evidence (log snippet, screenshot, trace, or failing test)
- Owner
- Target sprint

## Triage Workflow

1. Intake bug report.
2. Reproduce and validate severity.
3. Deduplicate against existing backlog.
4. Assign owner and target sprint.
5. Add or link regression test plan.
6. Track status through lifecycle.

## Status Lifecycle

- `new`
- `triaged`
- `in_progress`
- `ready_for_review`
- `done`
- `deferred`

## Bugfix Definition of Done

- Reproduction is documented and confirmed.
- Root cause is identified in PR summary.
- Regression test is added or explicitly justified as not applicable.
- All quality gates pass:
  - `python -m ruff check app tests`
  - `python -m pytest -q tests`
  - `RUN_INTEGRATION_TESTS=1 TEST_DATABASE_URL=... python -m pytest -q tests/integration`
  - Integration suite repeated once (anti-flaky)
- PR template sections are completed.

## Prioritization Rule

- Resolve all `P0` before any `P2/P3`.
- `P1` can be split into independent patches if that reduces merge risk.

## Related Runbooks

- Notification delivery diagnostics: `docs/quality/notification-troubleshooting-runbook.md`
