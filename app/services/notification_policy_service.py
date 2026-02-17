from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserNotificationPreference


class NotificationPreset(StrEnum):
    RECOMMENDED = "recommended"
    IMPORTANT = "important"
    ALL = "all"
    CUSTOM = "custom"


class NotificationEventType(StrEnum):
    AUCTION_OUTBID = "auction_outbid"
    AUCTION_FINISH = "auction_finish"
    AUCTION_WIN = "auction_win"
    AUCTION_MOD_ACTION = "auction_mod_action"
    POINTS = "points"
    SUPPORT = "support"


@dataclass(slots=True)
class NotificationSettingsSnapshot:
    master_enabled: bool
    preset: NotificationPreset
    outbid_enabled: bool
    auction_finish_enabled: bool
    auction_win_enabled: bool
    auction_mod_actions_enabled: bool
    points_enabled: bool
    support_enabled: bool
    configured: bool


_PRESET_VALUES: dict[NotificationPreset, dict[str, bool]] = {
    NotificationPreset.RECOMMENDED: {
        "outbid_enabled": True,
        "auction_finish_enabled": True,
        "auction_win_enabled": True,
        "auction_mod_actions_enabled": True,
        "points_enabled": True,
        "support_enabled": True,
    },
    NotificationPreset.IMPORTANT: {
        "outbid_enabled": False,
        "auction_finish_enabled": True,
        "auction_win_enabled": True,
        "auction_mod_actions_enabled": True,
        "points_enabled": False,
        "support_enabled": True,
    },
    NotificationPreset.ALL: {
        "outbid_enabled": True,
        "auction_finish_enabled": True,
        "auction_win_enabled": True,
        "auction_mod_actions_enabled": True,
        "points_enabled": True,
        "support_enabled": True,
    },
}

_EVENT_TO_FIELD: dict[NotificationEventType, str] = {
    NotificationEventType.AUCTION_OUTBID: "outbid_enabled",
    NotificationEventType.AUCTION_FINISH: "auction_finish_enabled",
    NotificationEventType.AUCTION_WIN: "auction_win_enabled",
    NotificationEventType.AUCTION_MOD_ACTION: "auction_mod_actions_enabled",
    NotificationEventType.POINTS: "points_enabled",
    NotificationEventType.SUPPORT: "support_enabled",
}

_EVENT_TO_ACTION_KEY: dict[NotificationEventType, str] = {
    NotificationEventType.AUCTION_OUTBID: "outbid",
    NotificationEventType.AUCTION_FINISH: "finish",
    NotificationEventType.AUCTION_WIN: "win",
    NotificationEventType.AUCTION_MOD_ACTION: "mod",
    NotificationEventType.POINTS: "points",
    NotificationEventType.SUPPORT: "support",
}

_ACTION_KEY_TO_EVENT: dict[str, NotificationEventType] = {
    value: key for key, value in _EVENT_TO_ACTION_KEY.items()
}


def _normalize_preset(raw: str | None) -> NotificationPreset:
    if raw is None:
        return NotificationPreset.RECOMMENDED
    try:
        return NotificationPreset(raw)
    except ValueError:
        return NotificationPreset.RECOMMENDED


def notification_event_action_key(event_type: NotificationEventType) -> str:
    return _EVENT_TO_ACTION_KEY[event_type]


def notification_event_from_action_key(action_key: str) -> NotificationEventType | None:
    return _ACTION_KEY_TO_EVENT.get(action_key)


def _snapshot_from_row(*, user: User, row: UserNotificationPreference | None) -> NotificationSettingsSnapshot:
    preset = _normalize_preset(getattr(row, "preset", None))
    if row is None or preset != NotificationPreset.CUSTOM:
        values = _PRESET_VALUES.get(preset, _PRESET_VALUES[NotificationPreset.RECOMMENDED])
    else:
        values = {
            "outbid_enabled": bool(row.outbid_enabled),
            "auction_finish_enabled": bool(row.auction_finish_enabled),
            "auction_win_enabled": bool(row.auction_win_enabled),
            "auction_mod_actions_enabled": bool(row.auction_mod_actions_enabled),
            "points_enabled": bool(row.points_enabled),
            "support_enabled": bool(row.support_enabled),
        }

    return NotificationSettingsSnapshot(
        master_enabled=bool(user.is_notifications_enabled),
        preset=preset,
        outbid_enabled=values["outbid_enabled"],
        auction_finish_enabled=values["auction_finish_enabled"],
        auction_win_enabled=values["auction_win_enabled"],
        auction_mod_actions_enabled=values["auction_mod_actions_enabled"],
        points_enabled=values["points_enabled"],
        support_enabled=values["support_enabled"],
        configured=row is not None and row.configured_at is not None,
    )


async def get_or_create_notification_preferences(
    session: AsyncSession,
    *,
    user_id: int,
) -> UserNotificationPreference:
    row = await session.scalar(
        select(UserNotificationPreference).where(UserNotificationPreference.user_id == user_id)
    )
    if row is not None:
        return row

    now_utc = datetime.now(timezone.utc)
    row = UserNotificationPreference(
        user_id=user_id,
        preset=NotificationPreset.RECOMMENDED.value,
        outbid_enabled=True,
        auction_finish_enabled=True,
        auction_win_enabled=True,
        auction_mod_actions_enabled=True,
        points_enabled=True,
        support_enabled=True,
        configured_at=None,
        updated_at=now_utc,
    )
    session.add(row)
    await session.flush()
    return row


async def load_notification_settings(
    session: AsyncSession,
    *,
    user_id: int,
) -> NotificationSettingsSnapshot | None:
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        return None
    row = await session.scalar(
        select(UserNotificationPreference).where(UserNotificationPreference.user_id == user.id)
    )
    return _snapshot_from_row(user=user, row=row)


async def load_notification_settings_by_tg_user_id(
    session: AsyncSession,
    *,
    tg_user_id: int,
) -> NotificationSettingsSnapshot | None:
    user = await session.scalar(select(User).where(User.tg_user_id == tg_user_id))
    if user is None:
        return None
    row = await session.scalar(
        select(UserNotificationPreference).where(UserNotificationPreference.user_id == user.id)
    )
    return _snapshot_from_row(user=user, row=row)


async def set_notification_preset(
    session: AsyncSession,
    *,
    user_id: int,
    preset: NotificationPreset,
    mark_configured: bool = True,
) -> NotificationSettingsSnapshot | None:
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        return None

    row = await get_or_create_notification_preferences(session, user_id=user_id)
    now_utc = datetime.now(timezone.utc)
    row.preset = preset.value
    row.updated_at = now_utc
    if mark_configured:
        row.configured_at = now_utc

    preset_values = _PRESET_VALUES.get(preset)
    if preset_values is not None:
        row.outbid_enabled = preset_values["outbid_enabled"]
        row.auction_finish_enabled = preset_values["auction_finish_enabled"]
        row.auction_win_enabled = preset_values["auction_win_enabled"]
        row.auction_mod_actions_enabled = preset_values["auction_mod_actions_enabled"]
        row.points_enabled = preset_values["points_enabled"]
        row.support_enabled = preset_values["support_enabled"]

    await session.flush()
    return _snapshot_from_row(user=user, row=row)


async def set_master_notifications_enabled(
    session: AsyncSession,
    *,
    user_id: int,
    enabled: bool,
) -> NotificationSettingsSnapshot | None:
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        return None
    user.is_notifications_enabled = enabled
    now_utc = datetime.now(timezone.utc)
    user.updated_at = now_utc

    row = await get_or_create_notification_preferences(session, user_id=user.id)
    row.updated_at = now_utc
    if row.configured_at is None:
        row.configured_at = now_utc

    await session.flush()
    return _snapshot_from_row(user=user, row=row)


async def toggle_notification_event(
    session: AsyncSession,
    *,
    user_id: int,
    event_type: NotificationEventType,
) -> NotificationSettingsSnapshot | None:
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        return None

    row = await get_or_create_notification_preferences(session, user_id=user_id)
    row.preset = NotificationPreset.CUSTOM.value
    row.configured_at = row.configured_at or datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)

    field_name = _EVENT_TO_FIELD[event_type]
    current = bool(getattr(row, field_name))
    setattr(row, field_name, not current)

    await session.flush()
    return _snapshot_from_row(user=user, row=row)


async def set_notification_event_enabled(
    session: AsyncSession,
    *,
    user_id: int,
    event_type: NotificationEventType,
    enabled: bool,
    mark_configured: bool = True,
) -> NotificationSettingsSnapshot | None:
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        return None

    row = await get_or_create_notification_preferences(session, user_id=user_id)
    now_utc = datetime.now(timezone.utc)
    row.preset = NotificationPreset.CUSTOM.value
    row.updated_at = now_utc
    if mark_configured and row.configured_at is None:
        row.configured_at = now_utc

    field_name = _EVENT_TO_FIELD[event_type]
    setattr(row, field_name, enabled)

    await session.flush()
    return _snapshot_from_row(user=user, row=row)


async def is_notification_allowed(
    session: AsyncSession,
    *,
    tg_user_id: int,
    event_type: NotificationEventType,
) -> bool:
    snapshot = await load_notification_settings_by_tg_user_id(session, tg_user_id=tg_user_id)
    if snapshot is None:
        return True
    if not snapshot.master_enabled:
        return False

    if event_type == NotificationEventType.AUCTION_OUTBID:
        return snapshot.outbid_enabled
    if event_type == NotificationEventType.AUCTION_FINISH:
        return snapshot.auction_finish_enabled
    if event_type == NotificationEventType.AUCTION_WIN:
        return snapshot.auction_win_enabled
    if event_type == NotificationEventType.AUCTION_MOD_ACTION:
        return snapshot.auction_mod_actions_enabled
    if event_type == NotificationEventType.POINTS:
        return snapshot.points_enabled
    return snapshot.support_enabled
