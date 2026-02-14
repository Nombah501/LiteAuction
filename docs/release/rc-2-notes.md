# Release Candidate 2 Notes

Date: 2026-02-14
Last automated re-check: 2026-02-14
Branch: `main`

## Scope Included

- Sprint 32 trust/risk implementation increments.
- High-risk publish gate for sellers without assigned guarantor.
- Appeals SLA and escalation visibility improvements in web admin.
- Trust indicators expansion across user/auction/appeal/signal views.
- Post-trade feedback foundation and moderation controls.
- User profile reputation summary based on trade feedback.

## Merged Release Increments

- PR #44: user risk snapshot indicators in admin.
- PR #45: high-risk guarantor publish gate.
- PR #46: appeals SLA states and escalation markers.
- PR #47: trust indicators on admin user/auction lists.
- PR #48: trust indicators on appeals/signals views.
- PR #49: post-trade feedback foundation + moderation view.
- PR #50: manage user reputation summary.

## Automated Verification (latest)

- `.venv/bin/python -m ruff check app tests alembic` -> PASS
- `.venv/bin/python -m pytest -q tests` -> PASS (`42 passed, 1 skipped`)
- Integration run #1 -> PASS (`90 passed`)
- Integration run #2 (anti-flaky) -> PASS (`90 passed`)

## Manual QA Status

- RC-2 matrix prepared: `docs/release/rc-2-manual-qa-matrix.md`.
- Execution status: pending manual run/sign-off.

## Current Verdict

- Automated quality gates: PASS
- Manual QA evidence: IN PROGRESS
- RC status: HOLD (until matrix sign-off)

## Go-Live Checklist

- [x] Code quality gates and anti-flaky rerun are green.
- [ ] Manual RC-2 matrix finalized.
- [ ] Product/owner rollout window confirmed.

## Rollback Guidance

If post-release validation fails:

1. Revert only the failing increment(s) from PR #44-#50.
2. Re-run lint + unit + integration gates.
3. Keep independent stable trust/reputation increments unless directly implicated.
