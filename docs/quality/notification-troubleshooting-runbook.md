# Notification Troubleshooting Runbook

## Scope

This runbook helps support and moderators diagnose why notification was:

- delivered
- skipped
- deferred/delayed

for Telegram DM notification flows.

## 1) User-Level Checks

1. Confirm user started private chat with the bot (`/start`).
2. Confirm user did not disable notifications in `/settings`:
   - global switch ON
   - relevant event type ON
3. Confirm user did not mute/snooze the specific lot (`/settings`, "Пауза по лоту").
4. Confirm quiet hours status in `/settings` (user timezone window).
5. If user clicked an old button, ask to reopen `/settings` (stale callback protection).

## 2) System-Level Checks

1. Check bot process health and polling/webhook status.
2. Confirm Redis is reachable (used for debounce/digest/deferred counters).
3. Confirm latest migrations are applied (notification preference/snooze/quiet-hours tables/columns).
4. Check delivery diagnostics logs for the exact `tg_user_id`.

## 3) Log Queries

Use stable prefixes from diagnostics:

- decision logs: `notification_delivery_decision`
- failure logs: `notification_delivery_failed`
- metric logs: `notification_metric`

Examples:

- user decision trail:
  - `notification_delivery_decision tg_user_id=<id>`
- all blocked decisions:
  - `notification_delivery_decision allowed=False`
- quiet-hours suppression:
  - `notification_delivery_decision reason=quiet_hours_deferred`
- transport failures:
  - `notification_delivery_failed tg_user_id=<id>`

## 4) Common Signatures and Remediation

- `reason=blocked_master`
  - User globally disabled notifications.
  - Remediation: user enables global switch in `/settings`.

- `reason=blocked_event_toggle`
  - Specific event type disabled by user.
  - Remediation: re-enable event in `/settings`.

- `reason=blocked_auction_snooze`
  - Lot-level snooze active.
  - Remediation: remove snooze in `/settings` or wait for expiry.

- `reason=quiet_hours_deferred`
  - Notification intentionally deferred by quiet-hours policy.
  - Remediation: wait for quiet window end or disable quiet hours.

- `failure_class=TelegramBadRequest reason=bad_request`
  - Telegram rejected request payload/chat context.
  - Remediation: verify `tg_user_id`, user bot access, and retry path.

- `failure_class=TelegramForbiddenError reason=forbidden`
  - Bot cannot DM user (blocked/restricted chat).
  - Remediation: ask user to unblock bot and send `/start`.

- `reason=telegram_api_error`
  - Upstream Telegram API temporary or request-level failure.
  - Remediation: retry; if repeated, escalate with timestamps.

## 5) Escalation Checklist

When escalating to engineering, include:

- `tg_user_id`
- event type (`auction_outbid`, `auction_win`, etc.)
- approximate timestamp (UTC)
- matching `notification_delivery_decision`/`notification_delivery_failed` lines
- user settings state from `/settings`

## 6) Operator Snapshot Command

Use `/notifstats` in private moderation topic for a compact Redis-based snapshot.

Output blocks:

- `sent total`: delivered notification attempts
- `suppressed total`: blocked/failed deliveries recorded as suppression
- `aggregated total`: anti-noise aggregation counters (digest/deferred flush/debounce)
- `Top suppression reasons (event/reason)`: most frequent suppression signatures

Interpretation quick-guide:

- high `suppressed total` with top `blocked_master` / `blocked_event_toggle`
  - user-level opt-out pattern; expected for users who tuned settings
- high `quiet_hours_deferred`
  - time-window deferral is active; validate timezone and quiet window in `/settings`
- high `forbidden` / `bad_request` / `telegram_api_error`
  - transport-level issues; use log queries from section 3 for exact `tg_user_id`
