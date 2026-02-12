# Sprint 30 Release Readiness Checklist

## Objective

Finalize release readiness after bugfix waves and visual foundation updates.

## Hard Gates

- [x] `python -m ruff check app tests`
- [x] `python -m pytest -q tests`
- [x] `RUN_INTEGRATION_TESTS=1 TEST_DATABASE_URL=postgresql+asyncpg://auction:auction@127.0.0.1:5432/auction_test python -m pytest -q tests/integration`
- [x] Repeat integration run once (anti-flaky)

## Manual QA Evidence

Use `docs/manual-qa/sprint-19.md` plus this visual sweep:

- [ ] Timeline filters and paging usable on desktop and mobile widths
- [ ] Empty-state messaging readable and styled consistently
- [ ] Error/forbidden/CSRF pages show clear recovery path (`Назад`, `На главную`)
- [ ] Keyboard focus ring visible on links, buttons, and form fields
- [ ] Table readability is acceptable at narrow viewport widths

Attach evidence to PR:

- Screenshots for timeline (desktop + mobile)
- Screenshot for denied/CSRF/error pages
- Short pass/fail matrix for MQ/TL and visual checks

Manual QA execution status: pending (requires interactive Telegram and admin web walkthrough)

## Rollback Plan

If release verification fails:

1. Revert Sprint 30 visual polish commit.
2. Re-run CI and integration checks.
3. Keep functional bugfix waves (S27/S28) intact.

## Sign-off

- Engineering: [ ]
- Product/Owner: [ ]
- Manual QA reviewer: [ ]
