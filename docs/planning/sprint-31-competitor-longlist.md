# Sprint 31 Competitor Longlist

This longlist is the starting set for the benchmark matrix in Sprint 31.

## Selection Rules

- 8-12 products total.
- At least 3 local C2C marketplaces.
- At least 2 niche transaction/dispute-heavy products.
- At least 2 Telegram-native/adjacent flows.
- 1-2 global references for trust and dispute UX quality.

## Candidate Set (v1)

### Local C2C

- Avito
- Yula
- Meshok

### Niche Transactions / Escrow-like

- FunPay
- Playerok
- Kwork

### Telegram-native / Adjacent

- Telega.in marketplace flow
- Fragment (transaction trust patterns)
- Typical channel-based auction bots (sample set from internal moderation queue)

### Global References

- eBay
- Mercari
- Facebook Marketplace

## Data Capture Checklist (per product)

- Entry friction: onboarding, KYC, posting friction.
- Trust signals: profile history, verification badges, deal stats.
- Pre-trade protection: restrictions for new sellers, risk scoring hints, escrow/guarantor options.
- Dispute mechanics: SLA visibility, escalation path, operator intervention quality.
- Reputation: two-sided feedback, weighting, anti-abuse controls.
- Incentives: points/credits/promotions and abuse prevention.
- Moderator productivity: queue UX, bulk actions, auditability.

## Output Contract

- Fill rows in `docs/planning/market-benchmark-matrix-template.md`.
- Record 2-3 notable patterns per product.
- For each pattern, tag feasibility for LiteAuction: `High`, `Medium`, `Low`.

## Expected Top-5 Pattern Families

- Risk-based trust gating for new/high-risk sellers.
- Guaranteed dispute SLA with explicit escalation timers.
- Structured reputation that survives one-off manipulation.
- Lightweight but visible anti-fraud friction before payment handoff.
- Incentive system tied to clear utility (not just static balance).
