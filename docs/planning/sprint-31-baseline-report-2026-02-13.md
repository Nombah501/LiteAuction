# Sprint 31 Baseline Report (2026-02-13)

## Snapshot

- Captured at (UTC): `2026-02-13 20:49:32`
- Data source: PostgreSQL `auction` DB in `liteauction-db`
- Method: queries from `docs/planning/sprint-31-baseline-metrics-playbook.md`
- Comparison windows:
  - Current: last 30 days
  - Previous: 30-60 days ago

## Key Metrics

### 1) Funnel Proxy (Published -> With Bid -> Completed)

| Period | Published auctions | Auctions with bid | Completed auctions | Published->Bid % | Bid->Completed % |
|---|---:|---:|---:|---:|---:|
| current | 4 | 3 | 4 | 75.00 | 133.33 |
| previous | 0 | 0 | 0 | n/a | n/a |

Interpretation:

- Very low data volume; previous window has no activity.
- `Bid->Completed > 100%` indicates denominator mismatch in proxy logic (completed includes auctions that may have no bids).

### 2) Complaint and Appeal Rates

| Period | Completed auctions | Complaints | Appeals | Complaints per 100 completed | Appeals per 100 complaints |
|---|---:|---:|---:|---:|---:|
| current | 4 | 6 | 0 | 150.00 | 0.00 |
| previous | 0 | 0 | 0 | n/a | n/a |

Interpretation:

- Complaint pressure is high relative to tiny completed volume.
- Appeals are currently not used (zero records).

### 3) Moderation Resolution Time

| Period | Queue | Median hours to resolve | P90 hours to resolve |
|---|---|---:|---:|
| current | complaints | 0.01 | 0.03 |

Interpretation:

- Current complaint processing is fast in this dataset (minutes-level), but sample size is small.
- No appeal resolution data yet.

### 4) Repeat Offender Proxy (Complaint Targets)

| Period | Targeted users | Repeat offender users (>=2 complaints) | Repeat offender share % |
|---|---:|---:|---:|
| current | 2 | 2 | 100.00 |

Interpretation:

- Complaints are concentrated on a very small set of users.
- Strong signal to prioritize risk-based guardrails for those profiles.

### 5) Reward System Baseline

Query result in the 30-day windows returned no rows.

All-time sanity counters:

- `points_ledger` entries: `0`
- net points: `0`

Interpretation:

- Reward economy is not yet active in this environment snapshot.
- KPI tracking for points utility should start after first non-zero production activity.

## Operational Caveats

- Dataset is sparse; window-over-window trend quality is limited.
- Funnel currently uses `auction_posts` as top-of-funnel proxy (no direct listing-view telemetry).
- Some rate metrics can look extreme due to small denominators.

## Recommended Sprint 31 Actions

1. Introduce a normalized funnel denominator for completion (completed auctions with at least one non-removed bid).
2. Add explicit event tracking for listing views and deal completion milestones.
3. Prioritize competitor patterns around pre-trade risk gating and dispute transparency.
4. Re-run this baseline at Sprint 31 close to check directional movement.
