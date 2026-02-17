# Sprint 44 Notification Digest Mode

## Goal

Reduce repetitive direct messages by grouping repeated outbid events into a compact digest message.

## Behavior

- Normal outbid notifications still use the regular immediate message path.
- When outbid notifications are suppressed by debounce in the same window:
  - suppressed events are accumulated per `(tg_user_id, auction_id)`
  - once repeated suppressions are detected, the user receives one digest message for that window

Digest example:

`Дайджест по лоту #abcd1234: за 3 мин ставку перебивали 4 раз.`

## Config

- `outbid_notification_digest_window_seconds` (default: `180`)
  - controls accumulation/emit window for outbid digest grouping

## Actionability

- Digest messages include the same open-auction action link (when available), so users can jump directly to the post.

## Notes for Maintainers

- Digest state is Redis-backed:
  - count key: `notif:digest:outbid:<tg_user_id>:<auction_id>:count`
  - emit lock key: `notif:digest:outbid:<tg_user_id>:<auction_id>:emit`
- Delivery policy remains tier-aware:
  - digest applies only if `should_include_notification_in_digest(event_type)` is `true`.
