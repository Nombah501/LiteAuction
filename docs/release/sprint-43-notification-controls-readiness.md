# Sprint 43 Notification Controls Readiness

## Objective

Ship actionable direct-notification controls that let users mute noisy categories and snooze specific auctions without leaving Telegram flows.

## Scope

- One-tap `Отключить тип уведомлений` action in direct notifications
- One-tap auction snooze action (`Пауза по лоту на 1ч`) for auction events
- Per-auction snooze storage with automatic expiry (`user_auction_notification_snoozes`)
- Settings panel visibility for active snoozes and one-tap remove actions
- Settings polish: quick re-enable buttons for disabled notification types
- Backward-safe callback parsing for stale/invalid mute and snooze payloads

## Validation Checklist

- [x] `python -m compileall app tests`
- [ ] `python -m ruff check app tests`
- [ ] `python -m pytest -q tests`

Notes:

- Runtime image in this environment does not include `ruff`/`pytest`; syntax validation was completed with `compileall`.

## Manual Smoke Checklist

- [ ] Receive outbid/finish/win/mod notification and see buttons:
  - `Открыть аукцион` (when link can be resolved)
  - `Пауза по лоту на 1ч`
  - `Отключить тип уведомлений`
- [ ] Tap `Пауза по лоту на 1ч` and verify next auction event is suppressed for ~1 hour
- [ ] Open `/settings` and verify active snooze appears with expiry and `Снять паузу #...`
- [ ] Tap `Снять паузу #...` and verify next auction event for the same lot is delivered
- [ ] Disable a category from a notification, then verify `/settings` shows `Включить: ...` quick action
- [ ] Tap `Включить: ...` and verify category resumes delivery
- [ ] Tap an outdated notification action payload and verify user sees a clear alert without traceback/noise in logs

## Rollout Plan

1. Deploy migration `0033_user_auc_notif_snooze`.
2. Deploy bot image with snooze callbacks and settings controls.
3. Verify bot health and polling status.
4. Execute manual smoke with two Telegram accounts across at least one active auction.

## Rollback Plan

If callback handling or suppression logic behaves unexpectedly:

1. Roll back bot image to previous stable release.
2. Keep migration in place (additive table, safe to keep unused).
3. Re-check direct notifications for legacy behavior.
4. Patch callback/action mapping in follow-up hotfix PR.
