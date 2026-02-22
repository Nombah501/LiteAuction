from __future__ import annotations

from datetime import UTC, datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.keyboards.auction import notification_settings_keyboard
from app.services.notification_policy_service import (
    AuctionNotificationSnoozeView,
    NotificationEventType,
    NotificationPreset,
    NotificationSettingsSnapshot,
    is_within_quiet_hours,
    notification_event_action_key,
)

_SETTINGS_TOGGLE_EVENTS: dict[str, NotificationEventType] = {
    "outbid": NotificationEventType.AUCTION_OUTBID,
    "finish": NotificationEventType.AUCTION_FINISH,
    "win": NotificationEventType.AUCTION_WIN,
    "mod": NotificationEventType.AUCTION_MOD_ACTION,
    "points": NotificationEventType.POINTS,
    "support": NotificationEventType.SUPPORT,
}

_EVENT_LABELS: dict[NotificationEventType, str] = {
    NotificationEventType.AUCTION_OUTBID: "Перебили ставку",
    NotificationEventType.AUCTION_FINISH: "Финиш моих лотов",
    NotificationEventType.AUCTION_WIN: "Победа в аукционе",
    NotificationEventType.AUCTION_MOD_ACTION: "Действия модерации",
    NotificationEventType.POINTS: "Баланс и points",
    NotificationEventType.SUPPORT: "Поддержка и апелляции",
}

_QUIET_HOURS_TIMEZONE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("UTC", "UTC"),
    ("Europe/Moscow", "Москва"),
    ("Asia/Yekaterinburg", "Екатеринбург"),
    ("Asia/Novosibirsk", "Новосибирск"),
    ("Asia/Vladivostok", "Владивосток"),
)
_QUIET_HOURS_TIMEZONE_CODES = {code for code, _ in _QUIET_HOURS_TIMEZONE_OPTIONS}


def _preset_title(preset: NotificationPreset) -> str:
    labels = {
        NotificationPreset.RECOMMENDED: "Рекомендуемые",
        NotificationPreset.IMPORTANT: "Только важные",
        NotificationPreset.ALL: "Все",
        NotificationPreset.CUSTOM: "Вручную",
    }
    return labels[preset]


def _format_snooze_expiry(expires_at: datetime) -> str:
    return expires_at.astimezone(UTC).strftime("%d.%m %H:%M UTC")


def _disabled_events(snapshot: NotificationSettingsSnapshot) -> list[NotificationEventType]:
    disabled: list[NotificationEventType] = []
    if not snapshot.outbid_enabled:
        disabled.append(NotificationEventType.AUCTION_OUTBID)
    if not snapshot.auction_finish_enabled:
        disabled.append(NotificationEventType.AUCTION_FINISH)
    if not snapshot.auction_win_enabled:
        disabled.append(NotificationEventType.AUCTION_WIN)
    if not snapshot.auction_mod_actions_enabled:
        disabled.append(NotificationEventType.AUCTION_MOD_ACTION)
    if not snapshot.points_enabled:
        disabled.append(NotificationEventType.POINTS)
    if not snapshot.support_enabled:
        disabled.append(NotificationEventType.SUPPORT)
    return disabled


def _format_quiet_hours_range(snapshot: NotificationSettingsSnapshot) -> str:
    return (
        f"{snapshot.quiet_hours_start_hour:02d}:00-"
        f"{snapshot.quiet_hours_end_hour:02d}:00 {snapshot.quiet_hours_timezone}"
    )


def _quiet_hours_status(snapshot: NotificationSettingsSnapshot) -> str:
    if not snapshot.quiet_hours_enabled:
        return "выключены"
    if is_within_quiet_hours(
        now_utc=datetime.now(UTC),
        start_hour=snapshot.quiet_hours_start_hour,
        end_hour=snapshot.quiet_hours_end_hour,
        timezone_name=snapshot.quiet_hours_timezone,
    ):
        return "активны сейчас"
    return "включены"


def _render_settings_text(
    snapshot: NotificationSettingsSnapshot,
    *,
    snoozes: list[AuctionNotificationSnoozeView] | None = None,
) -> str:
    global_state = "включены" if snapshot.master_enabled else "отключены"
    configured_state = "настроены" if snapshot.configured else "не настроены"
    lines = [
        "<b>Настройки уведомлений</b>",
        f"Глобально: <b>{global_state}</b>",
        f"Пресет: <b>{_preset_title(snapshot.preset)}</b>",
        f"Тихие часы: <b>{_quiet_hours_status(snapshot)}</b> ({_format_quiet_hours_range(snapshot)})",
        f"Часовой пояс тихих часов: <b>{snapshot.quiet_hours_timezone}</b>",
        f"Статус первичной настройки: <b>{configured_state}</b>",
        "",
        "Выберите пресет или переключите отдельные события кнопками ниже.",
    ]
    if snoozes:
        lines.extend(["", "<b>Пауза по отдельным лотам:</b>"])
        for snooze in snoozes:
            lines.append(
                f"- #{str(snooze.auction_id)[:8]} до {_format_snooze_expiry(snooze.expires_at)}"
            )

    disabled = _disabled_events(snapshot)
    if disabled:
        lines.extend(["", "<b>Отключенные типы:</b>"])
        for event_type in disabled:
            lines.append(f"- {_EVENT_LABELS[event_type]}")
    return "\n".join(lines)


def _settings_keyboard(
    snapshot: NotificationSettingsSnapshot,
    *,
    snoozes: list[AuctionNotificationSnoozeView] | None = None,
) -> InlineKeyboardMarkup:
    keyboard = notification_settings_keyboard(
        master_enabled=snapshot.master_enabled,
        preset=snapshot.preset.value,
        outbid_enabled=snapshot.outbid_enabled,
        auction_finish_enabled=snapshot.auction_finish_enabled,
        auction_win_enabled=snapshot.auction_win_enabled,
        auction_mod_actions_enabled=snapshot.auction_mod_actions_enabled,
        points_enabled=snapshot.points_enabled,
        support_enabled=snapshot.support_enabled,
    )
    rows = list(keyboard.inline_keyboard)
    for snooze in snoozes or []:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Снять паузу #{str(snooze.auction_id)[:8]}",
                    callback_data=f"dash:settings:unsnooze:{snooze.auction_id}",
                )
            ]
        )

    for event_type in _disabled_events(snapshot):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Включить: {_EVENT_LABELS[event_type]}",
                    callback_data=f"dash:settings:unmute:{notification_event_action_key(event_type)}",
                )
            ]
        )

    quiet_label = "ON" if snapshot.quiet_hours_enabled else "OFF"
    rows.append(
        [
            InlineKeyboardButton(
                text=f"Тихие часы: {quiet_label} {_format_quiet_hours_range(snapshot)}",
                callback_data="dash:settings:quiet:toggle",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=f"23:00-08:00 ({snapshot.quiet_hours_timezone})",
                callback_data="dash:settings:quiet:23-8",
            ),
            InlineKeyboardButton(
                text=f"00:00-07:00 ({snapshot.quiet_hours_timezone})",
                callback_data="dash:settings:quiet:0-7",
            ),
        ]
    )
    timezone_buttons: list[InlineKeyboardButton] = []
    for timezone_code, timezone_label in _QUIET_HOURS_TIMEZONE_OPTIONS:
        button_text = (
            f"[{timezone_label}]"
            if snapshot.quiet_hours_timezone == timezone_code
            else timezone_label
        )
        timezone_buttons.append(
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"dash:settings:tz:{timezone_code}",
            )
        )
    for offset in range(0, len(timezone_buttons), 2):
        rows.append(timezone_buttons[offset : offset + 2])
    rows.append(
        [
            InlineKeyboardButton(
                text="Отключить тихие часы",
                callback_data="dash:settings:quiet:off",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
