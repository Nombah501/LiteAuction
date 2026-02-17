# Sprint 45 Compatibility Hardening

## Goal

Make notification upgrades safer for existing deployments and stale client payloads.

## Migration Safety

- Hardened migrations to be idempotent:
  - `0033_user_auc_notif_snooze`
  - `0034_notif_quiet_hours`
- Both migrations now check existing schema state (tables/columns/constraints/indexes) before create/drop operations.

This reduces failure risk for partially applied environments and replayed migration steps.

## Legacy Callback Safety

- Callback parsing uses bounded splits and rejects malformed/extra-segment payloads safely.
- Legacy payloads continue to degrade gracefully to user-facing stale-button alerts instead of tracebacks.

## Deprecated Path Gating

- `is_notification_allowed(...)` is retained as a backward-compatible wrapper and explicitly routed through `notification_delivery_decision(...)`.
- New call sites should use decision-based API for reason-code observability.
