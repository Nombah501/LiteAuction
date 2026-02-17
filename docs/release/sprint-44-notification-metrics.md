# Sprint 44 Notification Metrics

## Goal

Add lightweight counters and structured log events for notification delivery outcomes so anti-noise impact can be observed directly from bot logs.

## Metric Dimensions

- `kind`: `sent`, `suppressed`, `aggregated`
- `event`: notification event type (`auction_outbid`, `auction_finish`, `auction_win`, `auction_mod_action`, `points`, `support`)
- `reason`: normalized reason code (`delivered`, `policy_blocked`, `bad_request`, `debounce_gate`, etc.)

Redis key format:

`notif:metrics:<kind>:<event>:<reason>`

## Emission Points

- `send_user_topic_message(...)`
  - `sent`: when message is delivered
  - `suppressed`: when blocked by policy or delivery fails (`bad_request`, `forbidden`, `telegram_api_error`, `unexpected_error`)
- outbid debounce gate
  - `suppressed` + `aggregated` with reason `debounce_gate` when duplicate outbid notification is throttled

## Log Format

Every increment logs one structured line:

- success: `notification_metric kind=<...> event=<...> reason=<...> count=<...> total=<...>`
- failure: `notification_metric_failed kind=<...> event=<...> reason=<...> count=<...> error=<...>`

This allows operators to inspect notification outcomes using plain log search without extra metrics tooling.
