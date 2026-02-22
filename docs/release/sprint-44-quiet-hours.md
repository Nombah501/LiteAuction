# Sprint 44 Quiet Hours

## Goal

Allow users to defer non-critical notifications during configured quiet hours while keeping critical/high-priority events immediate.

## User Controls

Quiet-hours controls are available in `/settings`:

- toggle quiet-hours on/off
- quick presets:
  - `23:00-08:00 (<selected timezone>)`
  - `00:00-07:00 (<selected timezone>)`
- timezone selector presets:
  - `UTC`
  - `Europe/Moscow`
  - `Asia/Yekaterinburg`
  - `Asia/Novosibirsk`
  - `Asia/Vladivostok`

## Delivery Behavior

- tier-aware policy is used:
  - `critical/high` events are delivered immediately
  - `normal/low` events are deferred during active quiet hours
- deferred events are counted per user/event type and surfaced on next eligible notification outside quiet hours

## Data Model

`user_notification_preferences` now stores:

- `quiet_hours_enabled` (`bool`)
- `quiet_hours_start_hour` (`0..23`)
- `quiet_hours_end_hour` (`0..23`)
- `quiet_hours_timezone` (IANA timezone string)

Migration: `0034_notif_quiet_hours`.

## Ops Notes

- suppression reason code: `quiet_hours_deferred`
- deferred-flush aggregation reason code: `quiet_hours_flushed`
