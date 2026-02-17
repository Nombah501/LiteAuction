# Sprint 42 Notification MVP Readiness

## Objective

Ship user-controlled direct notification preferences with safe defaults, actionable settings UI, and backward-safe delivery behavior.

## Scope

- DB schema for user notification preferences (`user_notification_preferences`)
- Notification policy service with presets and per-event toggles
- Private settings UI (`dash:settings`, `/settings`)
- First-start onboarding preset picker
- Policy enforcement in auction/moderation/points/support direct notifications

## Validation Checklist

- [x] `python -m py_compile app/db/models.py alembic/versions/0032_user_notification_preferences.py app/services/notification_policy_service.py app/services/private_topics_service.py app/bot/handlers/start.py app/bot/keyboards/auction.py app/bot/handlers/bid_actions.py app/services/auction_service.py app/bot/handlers/moderation.py app/bot/handlers/feedback.py app/bot/handlers/guarantor.py`
- [ ] `python -m ruff check app tests`
- [ ] `python -m pytest -q tests`

Notes:

- Runtime image used in this environment does not include `pytest`/`ruff`; syntax checks and runtime smoke were executed instead.

## Manual Smoke Checklist

- [x] `/settings` opens a working notification settings card in private chat
- [x] Preset buttons update state and show confirmation callback
- [x] Event toggles persist and affect routing decisions
- [x] Master switch OFF suppresses event-based delivery
- [x] First private `/start` shows compact preset onboarding when not configured

## Rollout Plan

1. Deploy migration `0032_user_notif_prefs`.
2. Deploy bot with policy and settings UI.
3. Verify health: `docker compose ps bot` is `healthy` and bot is polling.
4. Run smoke checks with two Telegram accounts:
   - outbid notification ON/OFF behavior,
   - preset changes,
   - onboarding shown only before first configuration.

## Rollback Plan

If unexpected notification suppression or routing issues are observed:

1. Disable policy-driven suppression by rolling back bot image to previous release.
2. Keep migration in place (table is additive and backward-safe).
3. Re-run smoke checks to confirm legacy notification behavior.
4. Investigate and patch policy mapping in a follow-up hotfix PR.
