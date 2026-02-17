from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.db.models import User, UserNotificationPreference
from app.services.notification_policy_service import (
    NotificationEventType,
    NotificationPreset,
    _snapshot_from_row,
    parse_notification_mute_callback_data,
    parse_notification_snooze_callback_data,
    notification_snooze_callback_data,
    notification_event_action_key,
    notification_event_from_action_key,
    notification_event_from_token,
)


def test_snapshot_defaults_to_recommended_when_row_missing() -> None:
    user = User(tg_user_id=123, is_notifications_enabled=True)

    snapshot = _snapshot_from_row(user=user, row=None)

    assert snapshot.master_enabled is True
    assert snapshot.preset == NotificationPreset.RECOMMENDED
    assert snapshot.outbid_enabled is True
    assert snapshot.configured is False


def test_snapshot_uses_custom_values_for_custom_preset() -> None:
    user = User(tg_user_id=123, is_notifications_enabled=False)
    row = UserNotificationPreference(
        user_id=1,
        preset=NotificationPreset.CUSTOM.value,
        outbid_enabled=False,
        auction_finish_enabled=True,
        auction_win_enabled=False,
        auction_mod_actions_enabled=True,
        points_enabled=False,
        support_enabled=True,
        configured_at=datetime.now(UTC),
    )

    snapshot = _snapshot_from_row(user=user, row=row)

    assert snapshot.master_enabled is False
    assert snapshot.preset == NotificationPreset.CUSTOM
    assert snapshot.outbid_enabled is False
    assert snapshot.auction_win_enabled is False
    assert snapshot.configured is True


def test_notification_event_action_key_roundtrip() -> None:
    for event_type in NotificationEventType:
        action_key = notification_event_action_key(event_type)
        assert notification_event_from_action_key(action_key) == event_type

    assert notification_event_from_action_key("unknown") is None


def test_notification_event_from_token_supports_action_key_and_raw_value() -> None:
    assert notification_event_from_token("outbid") == NotificationEventType.AUCTION_OUTBID
    assert notification_event_from_token("auction_outbid") == NotificationEventType.AUCTION_OUTBID
    assert notification_event_from_token("unknown") is None


def test_notification_snooze_callback_roundtrip() -> None:
    auction_id = UUID("12345678-1234-5678-1234-567812345678")
    callback_data = notification_snooze_callback_data(auction_id=auction_id, duration_minutes=90)

    assert callback_data == "notif:snooze:12345678-1234-5678-1234-567812345678:90"
    assert parse_notification_snooze_callback_data(callback_data) == (auction_id, 90)


def test_notification_snooze_callback_parser_rejects_invalid_payloads() -> None:
    assert parse_notification_snooze_callback_data("notif:snooze:not-a-uuid:60") is None
    assert parse_notification_snooze_callback_data("notif:snooze:12345678-1234-5678-1234-567812345678:-1") is None


def test_notification_snooze_callback_parser_accepts_legacy_without_duration() -> None:
    parsed = parse_notification_snooze_callback_data(
        "notif:snooze:12345678-1234-5678-1234-567812345678"
    )
    assert parsed == (UUID("12345678-1234-5678-1234-567812345678"), 60)


def test_notification_snooze_callback_clamps_duration_bounds() -> None:
    auction_id = UUID("12345678-1234-5678-1234-567812345678")

    short_callback = notification_snooze_callback_data(auction_id=auction_id, duration_minutes=0)
    long_callback = notification_snooze_callback_data(auction_id=auction_id, duration_minutes=100000)

    assert short_callback.endswith(":1")
    assert long_callback.endswith(":1440")


def test_parse_notification_mute_callback_data_supports_legacy_prefixes() -> None:
    assert parse_notification_mute_callback_data("notif:mute:outbid") == NotificationEventType.AUCTION_OUTBID
    assert parse_notification_mute_callback_data("notif:disable:win") == NotificationEventType.AUCTION_WIN
    assert (
        parse_notification_mute_callback_data("notif:off:auction_mod_action")
        == NotificationEventType.AUCTION_MOD_ACTION
    )
    assert parse_notification_mute_callback_data("notif:mute:unknown") is None
