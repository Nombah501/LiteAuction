# Sprint 31 Top-5 Practices Shortlist

This shortlist converts market-analysis goals into implementation-ready candidates for Sprint 32.

## Prioritization Method

Score formula from kickoff:

`priority = (Impact * Time-to-Value) / (Effort + Risk)`

Scale:

- Impact, Effort, Risk, Time-to-Value: 1-5

## Shortlist

| # | Practice | KPI Target | Impact | Effort | Risk | TTV | Priority |
|---|---|---|---:|---:|---:|---:|---:|
| 1 | Risk-based guarantor requirement for high-risk lots/users | -20% complaints per 100 completed | 5 | 3 | 2 | 4 | 4.00 |
| 2 | Dispute SLA transparency and auto-escalation timers | -30% p90 time-to-resolution | 5 | 3 | 2 | 4 | 4.00 |
| 3 | Public trust signals on seller profile (lightweight trust score) | +10% published->bid conversion on trusted sellers | 4 | 3 | 2 | 4 | 3.20 |
| 4 | Structured post-trade feedback (buyer<->seller) | +8% repeat trade participation | 4 | 4 | 2 | 3 | 2.00 |
| 5 | Points utility redemption (moderation fast-lane / listing boosts) | +15% active points users and non-zero spends | 4 | 4 | 3 | 3 | 1.71 |

## Why This Order

- Current baseline has high complaint pressure and concentrated complaint targets.
- Appeals and points activity are near-zero, so trust/dispute controls should precede incentive mechanics.
- Items 1-3 deliver near-term safety and conversion impact with moderate implementation cost.

## Sprint 32 Proposed Slices (Small PRs)

### P0: Risk + Dispute

1. Add deterministic risk flags for auction/user contexts in admin and moderation payloads.
2. Enforce guarantor requirement for high-risk paths with clear user-facing reason.
3. Add SLA deadline fields to dispute views and automated escalation markers in web/admin flows.

### P1: Trust Surface

4. Expose lightweight trust indicators on seller profile cards and auction views.
5. Add moderation override reasons for trust-related decisions (audit-friendly).

### P2: Reputation + Points Utility

6. Introduce post-trade feedback capture model and minimal moderation controls.
7. Enable one redeemable points utility path with strict anti-abuse limits and ledger auditing.

## Guardrails

- All new moderation-impacting actions must include scope checks, CSRF, and idempotency where applicable.
- Add integration tests for negative paths (scope denied, invalid token, duplicate submit).
- Keep each slice PR-sized and independently deployable.

## Decision Gate for Sprint 32 Start

Start Sprint 32 with items 1-3 only if:

- baseline report is accepted,
- competitor matrix is populated for at least 8 products,
- owners and KPI measurements are assigned.
