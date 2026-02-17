from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.bot.handlers.start import _preset_title, _render_settings_text
from app.services.notification_policy_service import (
    AuctionNotificationSnoozeView,
    NotificationPreset,
    NotificationSettingsSnapshot,
)


def test_preset_title_labels() -> None:
    assert _preset_title(NotificationPreset.RECOMMENDED) == "Рекомендуемые"
    assert _preset_title(NotificationPreset.IMPORTANT) == "Только важные"
    assert _preset_title(NotificationPreset.ALL) == "Все"
    assert _preset_title(NotificationPreset.CUSTOM) == "Вручную"


def test_render_settings_text_includes_state_markers() -> None:
    snapshot = NotificationSettingsSnapshot(
        master_enabled=False,
        preset=NotificationPreset.IMPORTANT,
        outbid_enabled=False,
        auction_finish_enabled=True,
        auction_win_enabled=True,
        auction_mod_actions_enabled=True,
        points_enabled=False,
        support_enabled=True,
        quiet_hours_enabled=False,
        quiet_hours_start_hour=23,
        quiet_hours_end_hour=8,
        configured=True,
    )

    text = _render_settings_text(snapshot)

    assert "Настройки уведомлений" in text
    assert "Глобально: <b>отключены</b>" in text
    assert "Пресет: <b>Только важные</b>" in text
    assert "Тихие часы:" in text
    assert "Статус первичной настройки: <b>настроены</b>" in text


def test_render_settings_text_includes_active_snoozes() -> None:
    snapshot = NotificationSettingsSnapshot(
        master_enabled=True,
        preset=NotificationPreset.RECOMMENDED,
        outbid_enabled=True,
        auction_finish_enabled=True,
        auction_win_enabled=True,
        auction_mod_actions_enabled=True,
        points_enabled=True,
        support_enabled=True,
        quiet_hours_enabled=False,
        quiet_hours_start_hour=23,
        quiet_hours_end_hour=8,
        configured=True,
    )
    snoozes = [
        AuctionNotificationSnoozeView(
            auction_id=UUID("12345678-1234-5678-1234-567812345678"),
            expires_at=datetime(2026, 2, 18, 10, 30, tzinfo=UTC),
        )
    ]

    text = _render_settings_text(snapshot, snoozes=snoozes)

    assert "Пауза по отдельным лотам" in text
    assert "#12345678" in text
    assert "18.02 10:30 UTC" in text


def test_render_settings_text_includes_disabled_types_block() -> None:
    snapshot = NotificationSettingsSnapshot(
        master_enabled=True,
        preset=NotificationPreset.CUSTOM,
        outbid_enabled=False,
        auction_finish_enabled=True,
        auction_win_enabled=False,
        auction_mod_actions_enabled=True,
        points_enabled=True,
        support_enabled=False,
        quiet_hours_enabled=True,
        quiet_hours_start_hour=23,
        quiet_hours_end_hour=8,
        configured=True,
    )

    text = _render_settings_text(snapshot)

    assert "Отключенные типы" in text
    assert "Перебили ставку" in text
    assert "Победа в аукционе" in text
    assert "Поддержка и апелляции" in text
