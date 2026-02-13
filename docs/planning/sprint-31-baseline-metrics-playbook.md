# Sprint 31 Baseline Metrics Playbook

This playbook defines how to collect baseline product metrics before market-driven changes.

## Scope Window

Collect two windows for comparison:

- Current window: last 30 days.
- Previous window: 30-60 days ago.

All timestamps are UTC.

## Data Sources

- `auctions`
- `auction_posts`
- `bids`
- `complaints`
- `appeals`
- `moderation_logs`
- `points_ledger`

## How to Run

Use `psql` in the DB container:

```bash
docker exec -it liteauction-db psql -U auction -d auction
```

Then run each query below. Keep outputs in a Sprint 31 baseline artifact.

## Query 1: Funnel Proxy (Published -> With Bid -> Completed)

```sql
WITH params AS (
    SELECT
        now() - interval '30 days' AS current_from,
        now() AS current_to,
        now() - interval '60 days' AS previous_from,
        now() - interval '30 days' AS previous_to
),
ranges AS (
    SELECT 'current' AS period, current_from AS ts_from, current_to AS ts_to FROM params
    UNION ALL
    SELECT 'previous' AS period, previous_from AS ts_from, previous_to AS ts_to FROM params
),
base AS (
    SELECT
        r.period,
        count(DISTINCT ap.auction_id) AS published_auctions,
        count(DISTINCT CASE WHEN b.id IS NOT NULL THEN ap.auction_id END) AS auctions_with_bid,
        count(DISTINCT CASE WHEN a.status IN ('ENDED', 'BOUGHT_OUT') THEN ap.auction_id END) AS completed_auctions
    FROM ranges r
    LEFT JOIN auction_posts ap ON ap.published_at >= r.ts_from AND ap.published_at < r.ts_to
    LEFT JOIN auctions a ON a.id = ap.auction_id
    LEFT JOIN bids b ON b.auction_id = ap.auction_id AND b.is_removed = false
    GROUP BY r.period
)
SELECT
    period,
    published_auctions,
    auctions_with_bid,
    completed_auctions,
    round(100.0 * auctions_with_bid / NULLIF(published_auctions, 0), 2) AS published_to_bid_pct,
    round(100.0 * completed_auctions / NULLIF(auctions_with_bid, 0), 2) AS bid_to_completed_pct
FROM base
ORDER BY period;
```

## Query 2: Complaint and Appeal Rates

```sql
WITH params AS (
    SELECT
        now() - interval '30 days' AS current_from,
        now() AS current_to,
        now() - interval '60 days' AS previous_from,
        now() - interval '30 days' AS previous_to
),
ranges AS (
    SELECT 'current' AS period, current_from AS ts_from, current_to AS ts_to FROM params
    UNION ALL
    SELECT 'previous' AS period, previous_from AS ts_from, previous_to AS ts_to FROM params
),
counts AS (
    SELECT
        r.period,
        count(DISTINCT CASE WHEN a.status IN ('ENDED', 'BOUGHT_OUT') THEN a.id END) AS completed_auctions,
        count(DISTINCT c.id) AS complaints,
        count(DISTINCT ap.id) AS appeals
    FROM ranges r
    LEFT JOIN auctions a ON a.created_at >= r.ts_from AND a.created_at < r.ts_to
    LEFT JOIN complaints c ON c.created_at >= r.ts_from AND c.created_at < r.ts_to
    LEFT JOIN appeals ap ON ap.created_at >= r.ts_from AND ap.created_at < r.ts_to
    GROUP BY r.period
)
SELECT
    period,
    completed_auctions,
    complaints,
    appeals,
    round(100.0 * complaints / NULLIF(completed_auctions, 0), 2) AS complaints_per_100_completed,
    round(100.0 * appeals / NULLIF(complaints, 0), 2) AS appeals_per_100_complaints
FROM counts
ORDER BY period;
```

## Query 3: Moderation Resolution Time (Complaints + Appeals)

```sql
WITH params AS (
    SELECT
        now() - interval '30 days' AS current_from,
        now() AS current_to,
        now() - interval '60 days' AS previous_from,
        now() - interval '30 days' AS previous_to
),
ranges AS (
    SELECT 'current' AS period, current_from AS ts_from, current_to AS ts_to FROM params
    UNION ALL
    SELECT 'previous' AS period, previous_from AS ts_from, previous_to AS ts_to FROM params
),
complaint_ttr AS (
    SELECT
        r.period,
        extract(epoch FROM (c.resolved_at - c.created_at)) / 3600.0 AS hours_to_resolve
    FROM ranges r
    JOIN complaints c ON c.created_at >= r.ts_from AND c.created_at < r.ts_to
    WHERE c.resolved_at IS NOT NULL
),
appeal_ttr AS (
    SELECT
        r.period,
        extract(epoch FROM (a.resolved_at - a.created_at)) / 3600.0 AS hours_to_resolve
    FROM ranges r
    JOIN appeals a ON a.created_at >= r.ts_from AND a.created_at < r.ts_to
    WHERE a.resolved_at IS NOT NULL
)
SELECT
    period,
    'complaints' AS queue,
    round(percentile_cont(0.5) WITHIN GROUP (ORDER BY hours_to_resolve)::numeric, 2) AS median_hours_to_resolve,
    round(percentile_cont(0.9) WITHIN GROUP (ORDER BY hours_to_resolve)::numeric, 2) AS p90_hours_to_resolve
FROM complaint_ttr
GROUP BY period
UNION ALL
SELECT
    period,
    'appeals' AS queue,
    round(percentile_cont(0.5) WITHIN GROUP (ORDER BY hours_to_resolve)::numeric, 2) AS median_hours_to_resolve,
    round(percentile_cont(0.9) WITHIN GROUP (ORDER BY hours_to_resolve)::numeric, 2) AS p90_hours_to_resolve
FROM appeal_ttr
GROUP BY period
ORDER BY period, queue;
```

## Query 4: Repeat Offender Proxy (Complaint Targets)

```sql
WITH params AS (
    SELECT
        now() - interval '30 days' AS current_from,
        now() AS current_to,
        now() - interval '60 days' AS previous_from,
        now() - interval '30 days' AS previous_to
),
ranges AS (
    SELECT 'current' AS period, current_from AS ts_from, current_to AS ts_to FROM params
    UNION ALL
    SELECT 'previous' AS period, previous_from AS ts_from, previous_to AS ts_to FROM params
),
target_counts AS (
    SELECT
        r.period,
        c.target_user_id,
        count(*) AS complaint_count
    FROM ranges r
    JOIN complaints c ON c.created_at >= r.ts_from AND c.created_at < r.ts_to
    WHERE c.target_user_id IS NOT NULL
    GROUP BY r.period, c.target_user_id
)
SELECT
    period,
    count(*) AS targeted_users,
    count(*) FILTER (WHERE complaint_count >= 2) AS repeat_offender_users,
    round(100.0 * count(*) FILTER (WHERE complaint_count >= 2) / NULLIF(count(*), 0), 2) AS repeat_offender_share_pct
FROM target_counts
GROUP BY period
ORDER BY period;
```

## Query 5: Reward System Baseline

```sql
WITH params AS (
    SELECT
        now() - interval '30 days' AS current_from,
        now() AS current_to,
        now() - interval '60 days' AS previous_from,
        now() - interval '30 days' AS previous_to
),
ranges AS (
    SELECT 'current' AS period, current_from AS ts_from, current_to AS ts_to FROM params
    UNION ALL
    SELECT 'previous' AS period, previous_from AS ts_from, previous_to AS ts_to FROM params
),
entry_window AS (
    SELECT
        r.period,
        p.user_id,
        p.amount,
        p.event_type
    FROM ranges r
    JOIN points_ledger p ON p.created_at >= r.ts_from AND p.created_at < r.ts_to
),
balances AS (
    SELECT
        r.period,
        p.user_id,
        sum(p.amount) AS balance
    FROM ranges r
    JOIN points_ledger p ON p.created_at < r.ts_to
    GROUP BY r.period, p.user_id
)
SELECT
    e.period,
    count(DISTINCT e.user_id) AS active_points_users,
    count(*) FILTER (WHERE e.event_type = 'MANUAL_ADJUSTMENT') AS manual_adjustment_ops,
    sum(e.amount) FILTER (WHERE e.amount > 0) AS earned_points,
    sum(-e.amount) FILTER (WHERE e.amount < 0) AS spent_points,
    round(percentile_cont(0.5) WITHIN GROUP (ORDER BY b.balance)::numeric, 2) AS median_balance
FROM entry_window e
LEFT JOIN balances b ON b.period = e.period AND b.user_id = e.user_id
GROUP BY e.period
ORDER BY e.period;
```

## Notes and Caveats

- There is no explicit listing-view telemetry yet; funnel uses published auctions as top-of-funnel proxy.
- Complaint/appeal rates should be interpreted with volume context (small denominator can inflate percentages).
- Repeat offender metric is a proxy based on complaint targets and does not imply confirmed abuse.
- If needed, lock timestamps by replacing `now()` with a fixed cutoff for repeatability.
