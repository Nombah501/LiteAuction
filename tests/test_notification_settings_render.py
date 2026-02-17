from __future__ import annotations

from app.bot.handlers.start import _preset_title, _render_settings_text
from app.services.notification_policy_service import NotificationPreset, NotificationSettingsSnapshot


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
        configured=True,
    )

    text = _render_settings_text(snapshot)

    assert "Настройки уведомлений" in text
    assert "Глобально: <b>отключены</b>" in text
    assert "Пресет: <b>Только важные</b>" in text
    assert "Статус первичной настройки: <b>настроены</b>" in text
