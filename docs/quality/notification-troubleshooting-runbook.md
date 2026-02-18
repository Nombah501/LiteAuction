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

Useful selector forms:

- `/notifstats 24h` or `/notifstats window=24h`: only 24h totals/delta and 24h top suppression reasons
- `/notifstats 7d` or `/notifstats window=7d`: only 7d totals and 7d top suppression reasons
- `/notifstats all` or `/notifstats window=all`: only all-time totals and all-time top suppression reasons

Output blocks:

- `All-time totals`: cumulative counters since last metrics reset/Redis flush
- `Last 24h totals`: rolling short-term activity window
- `24h delta vs previous 24h`: signed trend change for sent/suppressed/aggregated
- `Last 7d totals`: rolling weekly activity window
- `Top suppression reasons (24h)` / `Top suppression reasons (7d)`: short/medium window suppression signatures
- `Top suppression reasons (event/reason, all-time)`: most frequent suppression signatures

Interpretation quick-guide:

- high `suppressed total` with top `blocked_master` / `blocked_event_toggle`
  - user-level opt-out pattern; expected for users who tuned settings
- high `quiet_hours_deferred`
  - time-window deferral is active; validate timezone and quiet window in `/settings`
- high `forbidden` / `bad_request` / `telegram_api_error`
  - transport-level issues; use log queries from section 3 for exact `tg_user_id`

## 7) Metrics Retention and Drift Caveats

Counters are Redis-backed and have different retention semantics:

- all-time counters (`notif:metrics:*`)
  - monotonic totals; do not naturally decay over time
  - useful for long-range trend, less useful for short incident windows
- hourly buckets (`notif:metrics:h:<YYYYMMDDHH>:*`)
  - used to compute 24h/7d windows
  - current TTL is 10 days, so 7d window is stable with retention headroom

Expected drift patterns:

- low 24h with high all-time
  - historical spike no longer active; likely normal decay after incident
- gap after bot downtime/redeploy outage
  - window counters can under-report during ingestion interruptions
- sudden drop to near-zero across all windows
  - likely Redis flush/restart or namespace reset event

## 8) Safe Reset Workflow (Metrics Only)

Use reset only for operational reasons (for example, start a clean baseline after incident).

Pre-reset safety checks:

1. Capture current `/notifstats` output in incident ticket.
2. Record UTC timestamp and responder name.
3. Confirm no ongoing critical incident where historical counters are still needed.

Scoped reset procedure:

1. Count keys before reset:
   - `redis-cli --scan --pattern 'notif:metrics:*' | wc -l`
   - `redis-cli --scan --pattern 'notif:metrics:h:*' | wc -l`
2. Remove only notification metric keys:
   - `redis-cli --scan --pattern 'notif:metrics:*' | xargs -r redis-cli del`
   - `redis-cli --scan --pattern 'notif:metrics:h:*' | xargs -r redis-cli del`
3. Re-run `/notifstats` and verify totals are zeroed.
4. Add post-reset note to incident timeline with timestamp.

Rollback notes:

- preferred rollback: restore Redis dataset from backup/snapshot if available
- if restore is unavailable, treat reset timestamp as a new baseline and annotate analytics/reporting exports
- never run broad `FLUSHALL`/`FLUSHDB` for notification-only reset

## 9) Suppression Signatures -> Operator Actions

- `blocked_master`
  - meaning: user disabled all notifications
  - operator action: ask user to enable global switch in `/settings`
- `blocked_event_toggle`
  - meaning: event type disabled by user
  - operator action: ask user to re-enable the specific event toggle
- `blocked_auction_snooze`
  - meaning: lot-level mute/snooze still active
  - operator action: clear snooze in `/settings` or wait for expiry
- `quiet_hours_deferred`
  - meaning: message delayed by quiet-hours policy
  - operator action: validate user timezone + quiet-hour window in `/settings`
- `forbidden`
  - meaning: bot cannot deliver DM (blocked/restricted chat)
  - operator action: ask user to unblock bot and send `/start`
- `bad_request` / `telegram_api_error`
  - meaning: delivery payload/API-side failure
  - operator action: collect `tg_user_id` + UTC timestamp + log lines and escalate

## 10) Delta Interpretation and Alert Thresholds

`24h delta vs previous 24h` helps detect trend shifts without manual calculation.

Interpretation examples:

- `suppressed delta: +80`, `sent delta: -20`
  - suppression pressure increased while successful deliveries dropped
  - likely user-level policy spike, transport degradation, or both
- `suppressed delta: -40`, `sent delta: +35`
  - recovery pattern after mitigation or natural traffic normalization
- `aggregated delta: +120` with stable `sent delta`
  - anti-noise batching increased; check digest/debounce behavior and campaign bursts

Default alert thresholds (operator baseline):

- warning
  - `suppressed delta >= +30` OR
  - top 24h suppression reason contributes >= 35% of 24h suppressed total
- high
  - `suppressed delta >= +80` OR
  - `forbidden` / `bad_request` combined >= 25% of 24h suppressed total
- critical
  - `suppressed delta >= +150` OR
  - `sent delta <= -80` with simultaneous `suppressed delta > 0`

Thresholds are heuristics; compare with day-of-week traffic and active release windows.

Escalation path for threshold breach:

1. Re-check sections 1-4 to rule out user/config-only causes.
2. Capture evidence bundle:
   - `/notifstats` (full + filtered by dominant event/reason)
   - key log lines from section 3 with UTC timestamps
   - current runtime/policy toggles relevant to notifications
3. Open engineering escalation with severity (`warning`/`high`/`critical`) and include the evidence bundle.
4. If `high` or `critical` persists >15 minutes, start incident thread and update every 15 minutes until stabilized.

## 11) Operator Playbook Scenarios

Use these short playbooks when `/notifstats` shows a spike pattern. Each scenario references current fields (`Alert hints`, `Last 24h totals`, `24h delta`, and top suppression sections).

### Scenario A: User Opt-Out Spike (`blocked_master` / `blocked_event_toggle`)

Expected signals:

- `Alert hints` contains warning/high for suppression concentration
- `Last 24h totals` shows increased `suppressed total`
- `Top suppression reasons (24h)` dominated by `blocked_master` or `blocked_event_toggle`

Operator actions:

1. Run `/notifstats support` and `/notifstats auction_outbid` to check if spike is broad or event-specific.
2. Confirm no recent UI regression in `/settings` toggles.
3. Share user guidance macro: check global switch and event toggles.

Escalation template:

```text
[warning] notifstats opt-out spike
- UTC: <timestamp>
- suppressed delta: <value>
- top 24h reason: <event/reason> (<count>)
- impact: user-side opt-out pattern suspected
- action taken: user guidance broadcast / support macro update
```

### Scenario B: Transport Degradation (`forbidden` + `bad_request`)

Expected signals:

- `Alert hints` contains high signal for `forbidden+bad_request share`
- `Top suppression reasons (24h)` includes `support/forbidden` or `<event>/bad_request`
- `sent delta` flat/negative while suppression rises

Operator actions:

1. Run `/notifstats reason=forbidden` and `/notifstats reason=bad_request`.
2. Query logs (`notification_delivery_failed`) for sample `tg_user_id` and UTC timestamps.
3. For `forbidden`, ask affected users to unblock bot and send `/start`.
4. For `bad_request`, collect payload context and escalate to engineering.

Escalation template:

```text
[high] notifstats transport suppression spike
- UTC: <timestamp>
- forbidden+bad_request share (24h): <value>
- top reasons 24h: <list>
- sample tg_user_id set: <id1,id2,id3>
- action taken: user unblock guidance + failure logs attached
```

### Scenario C: Quiet-Hours Deferral Burst (`quiet_hours_deferred`)

Expected signals:

- `Top suppression reasons (24h)` shows `quiet_hours_deferred` as top-1
- `suppressed delta` positive with stable/healthy transport reasons
- support tickets mention delayed notifications, not missing notifications

Operator actions:

1. Validate timezone and quiet-hours windows in `/settings` for affected users.
2. Check whether burst coincides with expected local night window.
3. Confirm deferred summary flush appears after quiet window ends.

Escalation template:

```text
[warning] notifstats quiet-hours deferral burst
- UTC: <timestamp>
- top 24h reason: quiet_hours_deferred (<count>)
- affected event scope: <event or all>
- action taken: timezone/window verification with support
- escalation need: <yes/no>
```
