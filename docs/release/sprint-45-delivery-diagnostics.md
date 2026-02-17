# Sprint 45 Delivery Diagnostics

## Goal

Provide concise, searchable operator logs for notification delivery decisions and failure causes.

## Structured Log Events

- Decision log (INFO):
  - prefix: `notification_delivery_decision`
  - fields:
    - `tg_user_id`
    - `purpose`
    - `event`
    - `auction_id`
    - `allowed`
    - `reason`

- Failure log (WARNING):
  - prefix: `notification_delivery_failed`
  - fields:
    - `tg_user_id`
    - `purpose`
    - `event`
    - `reason`
    - `failure_class`
    - `error`

## Reason Codes

- decision reasons: `allowed`, `blocked_master`, `blocked_event_toggle`, `blocked_auction_snooze`, `quiet_hours_deferred`, `allow_no_user`
- failure reasons: `bad_request`, `forbidden`, `telegram_api_error`, `unexpected_error`

## Operator Usage

Recommended search queries in bot logs:

- all blocked deliveries: `notification_delivery_decision allowed=False`
- quiet-hours suppression: `notification_delivery_decision reason=quiet_hours_deferred`
- transport failures: `notification_delivery_failed`
- chat-level failures for a user: `notification_delivery_failed tg_user_id=<id>`
