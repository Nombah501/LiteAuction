# Sprint 52 Manual QA Checklist

## Goal

Validate v1.1 operator UX hardening for adaptive detail depth and preset telemetry safety.

Focus areas:

- keyboard-first triage continuity,
- deep-scroll context retention,
- telemetry insights behavior,
- RBAC/CSRF safety gates for telemetry-related endpoints.

## Preconditions

- Branch: `sprint-52-*`.
- Local services are up (`bot`, `admin`, `db`, `redis`).
- Migrations applied: `alembic upgrade head`.
- Test actors prepared:
  - owner/admin (has `user:ban`),
  - moderator without `user:ban`.

## Preflight

Run and confirm pass:

```bash
.venv/bin/python -m ruff check app tests
.venv/bin/python -m pytest -q tests
RUN_INTEGRATION_TESTS=1 TEST_DATABASE_URL=postgresql+asyncpg://auction:auction@127.0.0.1:5432/auction_test .venv/bin/python -m pytest -q tests/integration
```

## Test Cases

### S52-QA-01 Keyboard flow continuity

Steps:

1. Open a triage queue (`/complaints`, `/signals`, `/trade-feedback`, or `/appeals`).
2. Use `/` to focus search.
3. Use `j` and `k` to move focused row.
4. Use `o` and `Enter` to open/close details.
5. Use `x` to toggle row bulk selection.

Expected:

- Focused-row choreography remains stable while filtering and expanding details.
- No shortcut regressions in input controls.
- Bulk selection count updates correctly.

### S52-QA-02 Deep-scroll continuity on inline details

Steps:

1. Scroll deep into a long queue page.
2. Open an inline detail row.
3. Close details from the same row.

Expected:

- Scroll position is restored to prior context.
- Focus returns to the row-level invoker.
- Active filters and row position remain unchanged.

### S52-QA-03 Adaptive depth + override behavior

Steps:

1. Open details on rows with different risk/priority characteristics.
2. Confirm default adaptive reason string appears.
3. Switch override buttons (`Auto`, `Summary`, `Full`).

Expected:

- Secondary/audit sections collapse in summary depth and expand in full depth.
- Reason code updates reflect override/fallback behavior.
- Override does not break keyboard navigation.

### S52-QA-04 Telemetry insights advisory behavior

Steps:

1. Open each workflow queue page.
2. Locate telemetry panel and switch preset filter chips.
3. Verify current page context (queue filters/page state) is preserved.

Expected:

- Telemetry panel is visible on workflow pages.
- Advisory copy is present: telemetry does not automate moderation decisions.
- Preset filtering updates segment view without losing queue context.

### S52-QA-05 Safety checks (RBAC/CSRF)

Steps:

1. Call `/actions/workflow-presets` without auth.
2. Call `/actions/workflow-presets` with invalid CSRF token.
3. Call `/actions/workflow-presets/telemetry` with actor lacking `user:ban` scope.

Expected:

- Unauthorized requests are rejected.
- Invalid CSRF requests are rejected.
- Insufficient-scope telemetry reads are rejected.
- No workflow mutation occurs in denied paths.

## Result Template

```text
Manual QA (Sprint 52)

S52-QA-01: PASS/FAIL - notes
S52-QA-02: PASS/FAIL - notes
S52-QA-03: PASS/FAIL - notes
S52-QA-04: PASS/FAIL - notes
S52-QA-05: PASS/FAIL - notes

Evidence:
- queue screenshots: <paths>
- telemetry panel screenshots: <paths>
```
