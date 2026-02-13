# Sprint 31: Market Analysis Kickoff

## Objective

Define a practical market-backed roadmap for trust, moderation efficiency, and points utility.

## Scope

- Build baseline metrics from current product behavior.
- Benchmark 8-12 comparable products and extract reusable practices.
- Score candidate practices by impact, effort, risk, and time-to-value.
- Produce a prioritized implementation shortlist for Sprint 32+.

## Success Criteria

- Baseline metrics dashboard is defined and reproducible.
- Competitor benchmark matrix is filled for at least 8 products.
- Top-5 practices are selected with measurable hypotheses.
- Sprint 32 backlog is split into small PR-ready increments.

## Baseline Metrics (Current Product)

Track last 30 days and previous 30 days for trend comparison:

- Funnel: listing view -> bid -> completed trade.
- Moderation: queue volume, median time-to-first-action, median time-to-resolution.
- Risk: complaint rate, appeal rate, repeat offender rate.
- Retention: D7 and D30 return rate for active bidders/sellers.
- Rewards: points earners %, points balance distribution, manual adjustment volume.

## Competitor Set (Target)

Minimum mix:

- Local C2C marketplaces (general goods).
- Niche transaction platforms with dispute workflows.
- Telegram-native/Telegram-adjacent commerce flows.
- 1-2 global references for trust and dispute UX.

## Benchmark Dimensions

Use the same fields for each competitor:

- Identity and seller trust signals.
- Fraud prevention/guardrails at pre-trade stage.
- Dispute workflow quality (SLA, escalation, transparency).
- Reputation and post-trade feedback loop.
- Incentive system design (points/credits/perks) and abuse controls.
- Moderation tooling maturity and operator ergonomics.

## Scoring Model

For each candidate practice:

- Impact (1-5): expected KPI uplift.
- Effort (1-5): delivery complexity and dependency load.
- Risk (1-5): policy/legal/abuse/operational risk.
- Time-to-value (1-5): speed to measurable effect.

Recommended priority score:

`priority = (Impact * Time-to-value) / (Effort + Risk)`

## Two-Week Execution Plan

### Week 1

- Day 1: freeze metric definitions and owners.
- Day 2-3: gather baseline numbers from DB/logs and prepare snapshots.
- Day 4-5: competitor data collection pass #1 (at least 5 products).

### Week 2

- Day 6-7: competitor data collection pass #2 (complete 8-12 products).
- Day 8: scoring workshop and shortlist top-5 practices.
- Day 9: translate top-5 into incremental implementation backlog.
- Day 10: decision review and Sprint 32 scope lock.

## Deliverables

- Completed benchmark matrix.
- Competitor longlist and collection scope: `docs/planning/sprint-31-competitor-longlist.md`.
- Baseline metrics report (with formulas and extraction notes).
- Baseline SQL playbook: `docs/planning/sprint-31-baseline-metrics-playbook.md`.
- Current baseline snapshot: `docs/planning/sprint-31-baseline-report-2026-02-13.md`.
- Top-5 practices shortlist with rationale and KPI hypotheses.
- Sprint 32 implementation plan (small scoped PR sequence).

## Risks and Mitigations

- Risk: overfitting to competitors with different unit economics.
  - Mitigation: score by LiteAuction constraints first, not by popularity.
- Risk: analysis-only outcome without delivery.
  - Mitigation: convert each shortlisted practice into PR-sized tasks immediately.
- Risk: missing telemetry for key metrics.
  - Mitigation: include instrumentation tasks as Sprint 32 P0.

## Exit Decision

At sprint close, choose one:

- GO: proceed with top-3 items into Sprint 32 implementation.
- HOLD: add instrumentation or policy clarifications first.
- DROP: reject low-confidence practices and update shortlist.
