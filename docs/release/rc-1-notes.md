# Release Candidate 1 Notes

Date: 2026-02-13
Last automated re-check: 2026-02-13
Branch: `main`

## Scope Included

- Sprint 26: debug/triage foundation.
- Sprint 27-28: bugfix waves for timeline navigation, paging, retry idempotency, denied-scope back links.
- Sprint 29-30: visual foundation and final polish for admin web UX.
- Post-sprint hardening: safe return-path checks and moderation UX follow-up.

## Merged Release Increments

- PR #19: moderation UX improvements (modpanel unfreeze, richer sanction notices, `/start appeal_<ref>` path, queue enrichment, initial `/violators`).
- PR #20: appeals workflow (domain + migration + service, intake persistence, modpanel/web queues and actions).
- PR #21: appeal audit trail (new moderation actions, enum migration, web/modpanel logging, RC QA matrix refresh).
- PR #22: violators workflow enhancements (`by` + date filters, inline unban, filter-preserving pagination/actions, validation and integration coverage).

## Automated Verification (latest)

- `.venv/bin/python -m ruff check app tests alembic` -> PASS
- `.venv/bin/python -m pytest -q tests` -> PASS (`36 passed, 1 skipped`)
- Integration run #1 -> PASS (`37 passed`)
- Integration run #2 (anti-flaky) -> PASS (`37 passed`)

## Manual QA Status

- RC-1 matrix is filled and marked GO: `docs/release/rc-1-manual-qa-matrix.md`.
- Core moderation + appeals + violators cases are marked PASS in matrix evidence.

## Current Verdict

- Automated quality gates: PASS
- Manual QA evidence: PASS
- RC status: GO

## Go-Live Checklist

- [x] Code quality gates and anti-flaky rerun are green.
- [x] Appeals audit trail and violators workflows validated.
- [x] Manual RC matrix finalized.
- [ ] Product/owner final communication and rollout window confirmation.

## Rollback Guidance

If post-release validation fails:

1. Revert the latest failing increment only.
2. Re-run lint + unit + integration gates.
3. Keep unrelated stable moderation fixes intact unless directly implicated.
