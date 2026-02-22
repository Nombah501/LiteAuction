# Sprint 44 Notification Metrics

## Goal

Add lightweight counters and structured log events for notification delivery outcomes so anti-noise impact can be observed directly from bot logs.

## Metric Dimensions

- `kind`: `sent`, `suppressed`, `aggregated`
- `event`: notification event type (`auction_outbid`, `auction_finish`, `auction_win`, `auction_mod_action`, `points`, `support`)
- `reason`: normalized reason code (`delivered`, `allow_no_user`, `blocked_master`, `blocked_event_toggle`, `blocked_auction_snooze`, `quiet_hours_deferred`, `bad_request`, `forbidden`, `telegram_api_error`, `unexpected_error`, `debounce_gate`, `quiet_hours_flushed`)

Redis key format:

`notif:metrics:<kind>:<event>:<reason>`

## Emission Points

- `send_user_topic_message(...)`
  - `sent`: when message is delivered
  - `suppressed`: when blocked by policy (`blocked_master`, `blocked_event_toggle`, `blocked_auction_snooze`, `quiet_hours_deferred`) or delivery fails (`bad_request`, `forbidden`, `telegram_api_error`, `unexpected_error`)
  - `sent` with reason `allow_no_user`: user has no notification settings row yet
  - `aggregated` with reason `quiet_hours_flushed`: deferred notifications summary is flushed after quiet hours
- outbid debounce gate
  - `suppressed` + `aggregated` with reason `debounce_gate` when duplicate outbid notification is throttled

## Log Format

Every increment logs one structured line:

- success: `notification_metric kind=<...> event=<...> reason=<...> count=<...> total=<...>`
- failure: `notification_metric_failed kind=<...> event=<...> reason=<...> count=<...> error=<...>`

This allows operators to inspect notification outcomes using plain log search without extra metrics tooling.
