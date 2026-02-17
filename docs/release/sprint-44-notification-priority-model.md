# Sprint 44 Notification Priority Model

## Goal

Define stable notification priority tiers and delivery rules so anti-noise features (debounce, digest, quiet hours) use one shared policy contract.

## Event Priority Mapping

| Event type | Priority tier | Rationale |
|---|---|---|
| `auction_win` | `critical` | Winning outcome must be delivered immediately. |
| `auction_finish` | `high` | Auction closure for sellers is time-sensitive. |
| `auction_mod_action` | `high` | Moderation actions should not be delayed/suppressed by anti-noise controls. |
| `support` | `high` | Support and appeals updates are operationally important. |
| `auction_outbid` | `normal` | High-frequency candidate, safe for anti-noise controls. |
| `points` | `low` | Informational updates, suitable for aggregation/deferral. |

## Tier Delivery Rules

- `critical` / `high`:
  - `debounce_enabled = false`
  - `digest_enabled = false`
  - `defer_during_quiet_hours = false`
- `normal` / `low`:
  - `debounce_enabled = true`
  - `digest_enabled = true`
  - `defer_during_quiet_hours = true`

## Current Enforcement

- Outbid DM notifications check tier policy before applying Redis debounce gate.
- If policy disables debounce for an event, debounce key acquisition is skipped.

## Maintainer Notes

- Source of truth lives in `app/services/notification_policy_service.py`:
  - `NotificationPriorityTier`
  - `NotificationDeliveryPolicy`
  - `notification_priority_tier(...)`
  - `notification_delivery_policy(...)`
- Feature-specific code should call policy helpers instead of duplicating tier logic:
  - `should_apply_notification_debounce(...)`
  - `should_include_notification_in_digest(...)`
  - `should_defer_notification_during_quiet_hours(...)`
