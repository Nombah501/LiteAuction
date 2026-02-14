# Sprint 32 Implementation Plan

This plan translates Sprint 31 outputs into execution-ready increments.

Inputs:

- `docs/planning/sprint-31-baseline-report-2026-02-13.md`
- `docs/planning/sprint-31-top5-practices-shortlist.md`
- `docs/planning/market-benchmark-matrix-template.md`

## Sprint Goal

Reduce complaint pressure and improve trust transparency without introducing heavy user friction.

## Execution Status (Current)

- Done:
  - PR #44: user risk snapshot indicators in admin.
  - PR #45: high-risk guarantor publish gate.
  - PR #46: appeals SLA states and escalation markers.
  - PR #47: trust indicators on admin users and auctions lists.
  - PR #48: trust indicators on appeals and fraud signals lists.
  - PR #49: post-trade feedback foundation (bot intake + web moderation list).
  - PR #50: manage user reputation summary based on trade feedback.
  - PR #51: docs sync for sprint status and RC-2 notes.
  - PR #52: trade feedback moderation ergonomics.
  - PR #53: points utility v1 feedback priority boost.
  - PR #54: points utility v1.1 visibility slices.
- In progress:
  - Points redemption conversion metrics in moderation stats.
- Next:
  - Additional redeem mechanics after dual-boost and conversion baseline.

## P0 Scope (Recommended)

Implement shortlist items 1-3 first:

1. Risk-based guarantor requirement.
2. Dispute SLA transparency and auto-escalation markers.
3. Lightweight trust signals in admin/user-facing surfaces.

## Implemented Sequence

### PR-44: Risk Flag Foundation

- Add deterministic risk evaluation helper (auction + user context).
- Persist evaluation payload in moderation-friendly format.
- Expose risk reason labels in admin timeline/manage views.

Acceptance:

- Risk score and reasons visible in admin flow.
- Integration tests for low/high-risk classification and payload stability.

### PR-45: High-Risk Guarantor Gate

- Enforce guarantor requirement for high-risk auction paths.
- Add explicit user-facing reason when action is blocked.
- Add moderator override path with audit trail.

Acceptance:

- High-risk paths blocked without guarantor.
- Override requires scoped actor and is logged.

### PR-46: Dispute SLA Surfacing

- Display SLA deadline and escalation status in admin dispute views.
- Add proactive escalation marker when deadline is near/past.
- Keep current appeal escalation behavior unchanged.

Acceptance:

- SLA and escalation state visible in web panel.
- Integration tests for on-time and overdue states.

### PR-47 + PR-48: Trust Signal Surface Expansion

- Add lightweight trust badge/summary fields on manage user and auction-related admin views.
- Source trust indicators from existing complaint/ban/moderation signals.
- Keep scoring simple and explainable (no opaque model).

Acceptance:

- Trust summary consistently renders for users with and without history.
- Explanations remain stable under test fixtures.

### PR-49: Post-Trade Feedback Foundation

- Add `trade_feedback` domain table and migration.
- Add bot intake command `/tradefeedback <auction_id> <1..5> [comment]`.
- Add web moderation queue `/trade-feedback` with hide/unhide actions.

Acceptance:

- Feedback is accepted only for seller/winner on completed auctions.
- Web moderation list supports filtering/search and visibility actions.

### PR-50: Manage User Reputation Summary

- Add reputation KPI block on `/manage/user/{id}`.
- Show recent received feedback with status context.
- Link profile-level view to moderation queue.

Acceptance:

- Reputation summary renders for users with and without feedback history.
- Integration coverage validates summary math and table rendering.

## Next Sequence (Planned)

### PR-51: Reputation Moderation Ergonomics

- Add faster moderation filters (rating thresholds, hidden-only shortcuts, actor-based drilldown).
- Add moderation audit payload standardization for feedback visibility actions.

### PR-52: Points Utility v1

- Introduce first redeemable utility path for points.
- Add anti-abuse limits and ledger-safe spend semantics.
- Add basic user-facing usage docs and moderator controls.

### PR-54: Points Utility v1.1 Visibility

- Add points utility KPIs to web and bot moderation stats output.
- Add per-user boost usage counters on `/manage/user/{id}`.
- Add user-facing `/points` policy hints for boost cost and remaining daily limit.

### PR-55: Points Utility v2 Guarantor Priority Boost

- Add second redeem path: `/boostguarant <request_id>` for own open guarantor request.
- Add ledger event and anti-abuse limits/cost config for guarantor boosts.
- Extend points filtering and labels in bot/web for guarantor boost spends.

### PR-56: Points Utility v2.1 Conversion Metrics

- Add conversion-friendly points KPIs in `/modstats` and web dashboard.
- Track users with positive points balance vs unique redeemers in 7-day window.
- Split redeemers by utility path (feedback boost vs guarantor boost).

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
