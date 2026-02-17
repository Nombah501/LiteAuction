from __future__ import annotations

from datetime import UTC, datetime

from app.db.models import User, UserNotificationPreference
from app.services.notification_policy_service import NotificationPreset, _snapshot_from_row


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
