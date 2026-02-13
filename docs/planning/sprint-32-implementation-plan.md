# Sprint 32 Implementation Plan

This plan translates Sprint 31 outputs into execution-ready increments.

Inputs:

- `docs/planning/sprint-31-baseline-report-2026-02-13.md`
- `docs/planning/sprint-31-top5-practices-shortlist.md`
- `docs/planning/market-benchmark-matrix-template.md`

## Sprint Goal

Reduce complaint pressure and improve trust transparency without introducing heavy user friction.

## P0 Scope (Recommended)

Implement shortlist items 1-3 first:

1. Risk-based guarantor requirement.
2. Dispute SLA transparency and auto-escalation markers.
3. Lightweight trust signals in admin/user-facing surfaces.

## PR Sequence (Small Scoped)

### PR-43: Risk Flag Foundation

- Add deterministic risk evaluation helper (auction + user context).
- Persist evaluation payload in moderation-friendly format.
- Expose risk reason labels in admin timeline/manage views.

Acceptance:

- Risk score and reasons visible in admin flow.
- Integration tests for low/high-risk classification and payload stability.

### PR-44: High-Risk Guarantor Gate

- Enforce guarantor requirement for high-risk auction paths.
- Add explicit user-facing reason when action is blocked.
- Add moderator override path with audit trail.

Acceptance:

- High-risk paths blocked without guarantor.
- Override requires scoped actor and is logged.

### PR-45: Dispute SLA Surfacing

- Display SLA deadline and escalation status in admin dispute views.
- Add proactive escalation marker when deadline is near/past.
- Keep current appeal escalation behavior unchanged.

Acceptance:

- SLA and escalation state visible in web panel.
- Integration tests for on-time and overdue states.

### PR-46: Trust Signal Surface

- Add lightweight trust badge/summary fields on manage user and auction-related admin views.
- Source trust indicators from existing complaint/ban/moderation signals.
- Keep scoring simple and explainable (no opaque model).

Acceptance:

- Trust summary consistently renders for users with and without history.
- Explanations remain stable under test fixtures.

## Non-Goals for Sprint 32

- Full post-trade two-sided reputation system.
- Points redemption marketplace.
- Complex ML risk models.

## KPI Tracking During Sprint 32

- Complaints per 100 completed auctions.
- Share of complaints from repeat-target users.
- p90 complaint/dispute resolution time.
- Published->bid conversion for trusted vs non-trusted cohorts.

## Rollout and Safety

- Use staged rollout toggles per feature where possible.
- Keep rollback path simple (disable feature gate, preserve data).
- Every PR must include negative-path tests (scope denied, invalid token, duplicate submit).
