# Sprint 54: Queue SLA Health Contract

## Purpose

Define deterministic SLA health and aging semantics for moderation queues so operators get consistent urgency signals before UI rollout.

## Decision Surface

`app/services/queue_sla_health_service.py` exposes `decide_queue_sla_health(...)` and returns:

- `health_state`: `healthy`, `warning`, `critical`, `overdue`, `no_sla`, `closed`
- `aging_bucket`: `fresh`, `aging`, `stale`, `critical`, `overdue`, `unknown`, `closed`
- `reason_code`: deterministic reason for state assignment
- `countdown_seconds`: remaining seconds to deadline (or `None` when no SLA)
- `fallback_applied` and `fallback_notes`: explicit handling for missing or stale inputs

## Thresholds by Queue Context

| Queue context | Warning window | Critical window | Fresh max age | Aging max age | Stale max age |
|---|---:|---:|---:|---:|---:|
| `moderation` | 4h | 1h | 2h | 6h | 12h |
| `appeals` | 6h | 2h | 4h | 12h | 24h |
| `risk` | 2h | 30m | 1h | 3h | 8h |
| `feedback` | 8h | 3h | 6h | 18h | 36h |

## Deterministic Rules

1. Unknown queue context falls back to `moderation` with `fallback_notes += ["unknown_queue_context"]`.
2. Empty status falls back to `OPEN` with `fallback_notes += ["missing_status_assumed_open"]`.
3. Terminal statuses (`RESOLVED`, `REJECTED`, `CLOSED`, `DISMISSED`, `DONE`) return `health_state=closed`.
4. Missing deadline returns `health_state=no_sla` and keeps age-derived bucket.
5. Deadline elapsed returns `health_state=overdue`, `aging_bucket=overdue`, `countdown_seconds=0`.
6. Remaining time inside critical/warning windows maps to `critical` or `warning`; otherwise `healthy`.
7. Missing `created_at` produces `aging_bucket=unknown` with `fallback_notes += ["missing_created_at"]`.
8. Stale timing inputs are explicit:
   - future `created_at` -> `created_at_in_future`
   - `deadline_at < created_at` -> `deadline_before_created_at`

## Notes for Phase 2 Integration

- This contract intentionally does not prescribe CSS or UI wording; it stabilizes backend semantics first.
- S54-002 should map `health_state` and `aging_bucket` to operator-facing labels/chips while preserving existing queue context behavior.
