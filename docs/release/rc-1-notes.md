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
- PR #23: active `IN_REVIEW` workflow for appeals (take-in-work action in web/modpanel, active queue alignment, additional scope/pagination coverage).
- PR #24: one-time appeal SLA escalation (SLA fields + migration + watcher, overdue notification flow, overdue visibility in web/modpanel).
- PR #25: escalation audit trail (new `ESCALATE_APPEAL` moderation action + migration, persisted escalation logs with payload and system actor attribution).

## Automated Verification (latest)

- `.venv/bin/python -m ruff check app tests alembic` -> PASS
- `.venv/bin/python -m pytest -q tests` -> PASS (`36 passed, 1 skipped`)
- Integration run #1 -> PASS (`46 passed`)
- Integration run #2 (anti-flaky) -> PASS (`46 passed`)

## Manual QA Status

- RC-1 matrix is filled and marked GO: `docs/release/rc-1-manual-qa-matrix.md`.
- Core moderation + appeals + violators + SLA escalation cases are marked PASS in matrix evidence.

## Current Verdict

- Automated quality gates: PASS
- Manual QA evidence: PASS
- RC status: GO

## Go-Live Checklist

- [x] Code quality gates and anti-flaky rerun are green.
- [x] Appeals workflow, SLA escalation, and audit trail validated.
- [x] Manual RC matrix finalized.
- [x] Product/owner final communication and rollout window confirmation.

Rollout window confirmation:

- Product/owner communication: confirmed
- Planned rollout window: 2026-02-13 (post-merge window)

## Rollback Guidance

If post-release validation fails:

1. Revert the latest failing increment only.
2. Re-run lint + unit + integration gates.
3. Keep unrelated stable moderation fixes intact unless directly implicated.
