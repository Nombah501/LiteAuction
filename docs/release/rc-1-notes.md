# Release Candidate 1 Notes

Date: 2026-02-12
Branch: `post-sprint-rc-readiness`

## Scope Included

- Sprint 26: debug/triage foundation.
- Sprint 27-28: bugfix waves for timeline navigation, paging, retry idempotency, denied-scope back links.
- Sprint 29-30: visual foundation and final polish for admin web UX.
- Post-sprint: safe return-path hardening (`BUG-006`).

## Automated Verification (completed)

- `python -m ruff check app tests` -> PASS
- `python -m pytest -q tests` -> PASS (`29 passed, 1 skipped`)
- Integration run #1 -> PASS (`15 passed`)
- Integration run #2 (anti-flaky) -> PASS (`15 passed`)

## Manual QA Status (pending)

Consolidated manual QA is pending and must be completed before final release sign-off.

Use:

- `docs/manual-qa/sprint-19.md`
- `docs/release/sprint-30-readiness.md`

Required evidence:

- moderation queue before/after screenshots,
- timeline screenshots (desktop + mobile),
- denied/CSRF/action-error page screenshots,
- pass/fail matrix for MQ/TL and visual checklist items.

## Known Limitations

- No unresolved P0/P1 from current bug backlog.
- Manual QA evidence not yet attached in this branch.

## Rollback Guidance

If RC validation fails:

1. Revert the latest failing post-sprint commit(s).
2. Re-run automated quality gates.
3. Keep Sprint 27-28 functional fixes unless directly implicated.
