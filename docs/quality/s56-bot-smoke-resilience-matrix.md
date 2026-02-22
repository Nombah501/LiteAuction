# Sprint 56 Bot Smoke and Resilience Matrix

This matrix tracks critical cross-role bot journeys and degraded Telegram API branches.

## Coverage Matrix

| Case | Role / Journey | Scenario | Automated Check |
|---|---|---|---|
| SMK-U1 | User | `/points` default and detailed outputs stay actionable | `tests/integration/test_points_command.py` |
| SMK-S1 | Seller | Publish draft auction successfully | `tests/integration/test_publish_auction_flow.py::test_publish_command_activates_auction_and_persists_post` |
| SMK-S2 | Seller | Publish rollback on partial failure | `tests/integration/test_publish_auction_flow.py::test_publish_command_rolls_back_album_when_send_photo_fails` |
| SMK-M1 | Moderator | Resolve moderation callback and persist side effects | `tests/integration/test_moderation_callbacks_e2e.py::test_modrep_freeze_updates_db_and_refresh` |
| CONT-1 | Moderator continuity | Repeated callback remains idempotent (restart/retry-safe) | `tests/integration/test_moderation_callbacks_e2e.py::test_modrep_freeze_repeat_click_is_idempotent` |
| CONT-2 | Seller continuity | Activation-failure rollback avoids duplicate artifacts | `tests/integration/test_publish_auction_flow.py::test_publish_command_rolls_back_album_and_post_when_activation_fails` |
| RES-R1 | Telegram degraded API | Retry-after while refreshing post caption | `tests/test_auction_media_upgrade.py::test_refresh_post_logs_retry_after_and_stops` |
| RES-T1 | Telegram degraded API | Transient network error while refreshing post caption | `tests/test_auction_media_upgrade.py::test_refresh_post_logs_transient_network_error_and_stops` |
| RES-F1 | Telegram degraded API | Forbidden while attaching media during refresh | `tests/test_auction_media_upgrade.py::test_refresh_post_logs_forbidden_while_attaching_media` |
| RES-F2 | Telegram degraded API | Forbidden in moderation chat falls back to admins | `tests/test_moderation_topic_router.py::test_send_section_message_falls_back_to_admins_on_forbidden_in_moderation_chat` |
| RES-F3 | Telegram degraded API | Forbidden DM delivery is classified and suppressed | `tests/test_private_topics_delivery_diagnostics.py::test_delivery_failure_log_includes_forbidden_reason_and_metric` |
| RES-T2 | Telegram degraded API | Generic Telegram API error is classified and suppressed | `tests/test_private_topics_delivery_diagnostics.py::test_delivery_failure_log_includes_telegram_api_error_reason_and_metric` |

## CI Evidence Commands

- `python -m ruff check app tests`
- `python -m pytest -q tests`
- `RUN_INTEGRATION_TESTS=1 TEST_DATABASE_URL=postgresql+asyncpg://.../auction_test python -m pytest -q tests/integration`

For PR evidence, include command output lines with pass/fail counts and links to CI jobs.
