from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserAuctionNotificationSnooze, UserNotificationPreference


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


class NotificationPriorityTier(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


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
    quiet_hours_enabled: bool
    quiet_hours_start_hour: int
    quiet_hours_end_hour: int
    quiet_hours_timezone: str
    configured: bool


@dataclass(slots=True)
class AuctionNotificationSnoozeView:
    auction_id: uuid.UUID
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class NotificationDeliveryPolicy:
    priority_tier: NotificationPriorityTier
    debounce_enabled: bool
    digest_enabled: bool
    defer_during_quiet_hours: bool


@dataclass(frozen=True, slots=True)
class NotificationDeliveryDecision:
    allowed: bool
    reason: str


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

_AUCTION_SNOOZE_MINUTES_DEFAULT = 60
_AUCTION_SNOOZE_MINUTES_MAX = 24 * 60
_QUIET_HOURS_START_DEFAULT = 23
_QUIET_HOURS_END_DEFAULT = 8
_QUIET_HOURS_TIMEZONE_DEFAULT = "UTC"

_AUCTION_EVENT_TYPES: set[NotificationEventType] = {
    NotificationEventType.AUCTION_OUTBID,
    NotificationEventType.AUCTION_FINISH,
    NotificationEventType.AUCTION_WIN,
    NotificationEventType.AUCTION_MOD_ACTION,
}

_EVENT_PRIORITY_TIERS: dict[NotificationEventType, NotificationPriorityTier] = {
    NotificationEventType.AUCTION_OUTBID: NotificationPriorityTier.NORMAL,
    NotificationEventType.AUCTION_FINISH: NotificationPriorityTier.HIGH,
    NotificationEventType.AUCTION_WIN: NotificationPriorityTier.CRITICAL,
    NotificationEventType.AUCTION_MOD_ACTION: NotificationPriorityTier.HIGH,
    NotificationEventType.POINTS: NotificationPriorityTier.LOW,
    NotificationEventType.SUPPORT: NotificationPriorityTier.HIGH,
}

_PRIORITY_TIER_POLICIES: dict[NotificationPriorityTier, NotificationDeliveryPolicy] = {
    NotificationPriorityTier.CRITICAL: NotificationDeliveryPolicy(
        priority_tier=NotificationPriorityTier.CRITICAL,
        debounce_enabled=False,
        digest_enabled=False,
        defer_during_quiet_hours=False,
    ),
    NotificationPriorityTier.HIGH: NotificationDeliveryPolicy(
        priority_tier=NotificationPriorityTier.HIGH,
        debounce_enabled=False,
        digest_enabled=False,
        defer_during_quiet_hours=False,
    ),
    NotificationPriorityTier.NORMAL: NotificationDeliveryPolicy(
        priority_tier=NotificationPriorityTier.NORMAL,
        debounce_enabled=True,
        digest_enabled=True,
        defer_during_quiet_hours=True,
    ),
    NotificationPriorityTier.LOW: NotificationDeliveryPolicy(
        priority_tier=NotificationPriorityTier.LOW,
        debounce_enabled=True,
        digest_enabled=True,
        defer_during_quiet_hours=True,
    ),
}


def _normalize_preset(raw: str | None) -> NotificationPreset:
    if raw is None:
        return NotificationPreset.RECOMMENDED
    try:
        return NotificationPreset(raw)
    except ValueError:
        return NotificationPreset.RECOMMENDED


def _normalize_quiet_hour(raw: int | None, *, default: int) -> int:
    if raw is None:
        return default
    try:
        hour = int(raw)
    except (TypeError, ValueError):
        return default
    if 0 <= hour <= 23:
        return hour
    return default


def _normalize_quiet_hours_timezone(raw: str | None, *, default: str) -> str:
    if raw is None:
        return default

    timezone_name = str(raw).strip()
    if not timezone_name:
        return default

    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return default
    return timezone_name


def is_within_quiet_hours(
    *,
    now_utc: datetime,
    start_hour: int,
    end_hour: int,
    timezone_name: str = _QUIET_HOURS_TIMEZONE_DEFAULT,
) -> bool:
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    normalized_timezone = _normalize_quiet_hours_timezone(
        timezone_name,
        default=_QUIET_HOURS_TIMEZONE_DEFAULT,
    )
    local_hour = now_utc.astimezone(ZoneInfo(normalized_timezone)).hour

    if start_hour == end_hour:
        return False
    if start_hour < end_hour:
        return start_hour <= local_hour < end_hour
    return local_hour >= start_hour or local_hour < end_hour


def notification_event_action_key(event_type: NotificationEventType) -> str:
    return _EVENT_TO_ACTION_KEY[event_type]


def notification_event_from_action_key(action_key: str) -> NotificationEventType | None:
    return _ACTION_KEY_TO_EVENT.get(action_key)


def notification_event_from_token(token: str) -> NotificationEventType | None:
    event_type = notification_event_from_action_key(token)
    if event_type is not None:
        return event_type
    try:
        return NotificationEventType(token)
    except ValueError:
        return None


def notification_priority_tier(event_type: NotificationEventType) -> NotificationPriorityTier:
    return _EVENT_PRIORITY_TIERS[event_type]


def notification_delivery_policy(event_type: NotificationEventType) -> NotificationDeliveryPolicy:
    tier = notification_priority_tier(event_type)
    return _PRIORITY_TIER_POLICIES[tier]


def should_apply_notification_debounce(event_type: NotificationEventType) -> bool:
    return notification_delivery_policy(event_type).debounce_enabled


def should_include_notification_in_digest(event_type: NotificationEventType) -> bool:
    return notification_delivery_policy(event_type).digest_enabled


def should_defer_notification_during_quiet_hours(event_type: NotificationEventType) -> bool:
    return notification_delivery_policy(event_type).defer_during_quiet_hours


def default_auction_snooze_minutes() -> int:
    return _AUCTION_SNOOZE_MINUTES_DEFAULT


def notification_snooze_callback_data(*, auction_id: uuid.UUID, duration_minutes: int) -> str:
    duration = max(1, min(duration_minutes, _AUCTION_SNOOZE_MINUTES_MAX))
    return f"notif:snooze:{auction_id}:{duration}"


def parse_notification_snooze_callback_data(callback_data: str) -> tuple[uuid.UUID, int] | None:
    parts = callback_data.split(":", 3)
    if len(parts) not in {3, 4}:
        return None
    if parts[0] != "notif" or parts[1] != "snooze":
        return None

    try:
        auction_id = uuid.UUID(parts[2])
    except ValueError:
        return None

    if len(parts) == 3:
        return auction_id, _AUCTION_SNOOZE_MINUTES_DEFAULT

    if not parts[3].isdigit():
        return None
    duration = int(parts[3])
    if duration < 1:
        return None
    return auction_id, min(duration, _AUCTION_SNOOZE_MINUTES_MAX)


def parse_notification_mute_callback_data(callback_data: str) -> NotificationEventType | None:
    parts = callback_data.split(":", 2)
    if len(parts) != 3:
        return None
    if parts[0] != "notif" or parts[1] not in {"mute", "disable", "off"}:
        return None
    return notification_event_from_token(parts[2])


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

    quiet_hours_enabled = bool(getattr(row, "quiet_hours_enabled", False))
    quiet_hours_start_hour = _normalize_quiet_hour(
        getattr(row, "quiet_hours_start_hour", None),
        default=_QUIET_HOURS_START_DEFAULT,
    )
    quiet_hours_end_hour = _normalize_quiet_hour(
        getattr(row, "quiet_hours_end_hour", None),
        default=_QUIET_HOURS_END_DEFAULT,
    )
    quiet_hours_timezone = _normalize_quiet_hours_timezone(
        getattr(row, "quiet_hours_timezone", None),
        default=_QUIET_HOURS_TIMEZONE_DEFAULT,
    )

    return NotificationSettingsSnapshot(
        master_enabled=bool(user.is_notifications_enabled),
        preset=preset,
        outbid_enabled=values["outbid_enabled"],
        auction_finish_enabled=values["auction_finish_enabled"],
        auction_win_enabled=values["auction_win_enabled"],
        auction_mod_actions_enabled=values["auction_mod_actions_enabled"],
        points_enabled=values["points_enabled"],
        support_enabled=values["support_enabled"],
        quiet_hours_enabled=quiet_hours_enabled,
        quiet_hours_start_hour=quiet_hours_start_hour,
        quiet_hours_end_hour=quiet_hours_end_hour,
        quiet_hours_timezone=quiet_hours_timezone,
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
        quiet_hours_enabled=False,
        quiet_hours_start_hour=_QUIET_HOURS_START_DEFAULT,
        quiet_hours_end_hour=_QUIET_HOURS_END_DEFAULT,
        quiet_hours_timezone=_QUIET_HOURS_TIMEZONE_DEFAULT,
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


async def set_quiet_hours_settings(
    session: AsyncSession,
    *,
    user_id: int,
    enabled: bool,
    start_hour: int,
    end_hour: int,
    mark_configured: bool = True,
) -> NotificationSettingsSnapshot | None:
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        return None

    row = await get_or_create_notification_preferences(session, user_id=user_id)
    now_utc = datetime.now(timezone.utc)
    row.updated_at = now_utc
    if mark_configured and row.configured_at is None:
        row.configured_at = now_utc

    row.quiet_hours_enabled = bool(enabled)
    row.quiet_hours_start_hour = _normalize_quiet_hour(
        start_hour,
        default=_QUIET_HOURS_START_DEFAULT,
    )
    row.quiet_hours_end_hour = _normalize_quiet_hour(
        end_hour,
        default=_QUIET_HOURS_END_DEFAULT,
    )

    await session.flush()
    return _snapshot_from_row(user=user, row=row)


async def set_quiet_hours_timezone(
    session: AsyncSession,
    *,
    user_id: int,
    timezone_name: str,
    mark_configured: bool = True,
) -> NotificationSettingsSnapshot | None:
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        return None

    row = await get_or_create_notification_preferences(session, user_id=user_id)
    now_utc = datetime.now(timezone.utc)
    row.updated_at = now_utc
    if mark_configured and row.configured_at is None:
        row.configured_at = now_utc

    row.quiet_hours_timezone = _normalize_quiet_hours_timezone(
        timezone_name,
        default=_QUIET_HOURS_TIMEZONE_DEFAULT,
    )

    await session.flush()
    return _snapshot_from_row(user=user, row=row)


def _build_snooze_view(row: UserAuctionNotificationSnooze) -> AuctionNotificationSnoozeView:
    return AuctionNotificationSnoozeView(
        auction_id=row.auction_id,
        expires_at=row.expires_at,
    )


async def _delete_expired_snoozes(session: AsyncSession, *, user_id: int) -> None:
    await session.execute(
        delete(UserAuctionNotificationSnooze).where(
            UserAuctionNotificationSnooze.user_id == user_id,
            UserAuctionNotificationSnooze.expires_at <= datetime.now(timezone.utc),
        )
    )


async def list_active_auction_notification_snoozes(
    session: AsyncSession,
    *,
    user_id: int,
    limit: int = 5,
) -> list[AuctionNotificationSnoozeView]:
    await _delete_expired_snoozes(session, user_id=user_id)

    rows = await session.scalars(
        select(UserAuctionNotificationSnooze)
        .where(
            UserAuctionNotificationSnooze.user_id == user_id,
            UserAuctionNotificationSnooze.expires_at > datetime.now(timezone.utc),
        )
        .order_by(UserAuctionNotificationSnooze.expires_at.asc())
        .limit(max(limit, 1))
    )
    return [_build_snooze_view(row) for row in rows]


async def set_auction_notification_snooze(
    session: AsyncSession,
    *,
    user_id: int,
    auction_id: uuid.UUID,
    duration_minutes: int = 60,
) -> AuctionNotificationSnoozeView:
    await _delete_expired_snoozes(session, user_id=user_id)
    now_utc = datetime.now(timezone.utc)
    duration = max(1, min(duration_minutes, _AUCTION_SNOOZE_MINUTES_MAX))
    expires_at = now_utc + timedelta(minutes=duration)

    row = await session.scalar(
        select(UserAuctionNotificationSnooze).where(
            UserAuctionNotificationSnooze.user_id == user_id,
            UserAuctionNotificationSnooze.auction_id == auction_id,
        )
    )
    if row is None:
        row = UserAuctionNotificationSnooze(
            user_id=user_id,
            auction_id=auction_id,
            expires_at=expires_at,
            updated_at=now_utc,
        )
        session.add(row)
    else:
        row.expires_at = expires_at
        row.updated_at = now_utc

    await session.flush()
    return _build_snooze_view(row)


async def set_auction_notification_snooze_by_tg_user_id(
    session: AsyncSession,
    *,
    tg_user_id: int,
    auction_id: uuid.UUID,
    duration_minutes: int = 60,
) -> AuctionNotificationSnoozeView | None:
    user = await session.scalar(select(User).where(User.tg_user_id == tg_user_id))
    if user is None:
        return None
    return await set_auction_notification_snooze(
        session,
        user_id=user.id,
        auction_id=auction_id,
        duration_minutes=duration_minutes,
    )


async def clear_auction_notification_snooze(
    session: AsyncSession,
    *,
    user_id: int,
    auction_id: uuid.UUID,
) -> bool:
    row = await session.scalar(
        select(UserAuctionNotificationSnooze).where(
            UserAuctionNotificationSnooze.user_id == user_id,
            UserAuctionNotificationSnooze.auction_id == auction_id,
        )
    )
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


async def is_auction_notification_snoozed_by_tg_user_id(
    session: AsyncSession,
    *,
    tg_user_id: int,
    auction_id: uuid.UUID,
) -> bool:
    user = await session.scalar(select(User).where(User.tg_user_id == tg_user_id))
    if user is None:
        return False

    await _delete_expired_snoozes(session, user_id=user.id)
    row = await session.scalar(
        select(UserAuctionNotificationSnooze.id).where(
            UserAuctionNotificationSnooze.user_id == user.id,
            UserAuctionNotificationSnooze.auction_id == auction_id,
            UserAuctionNotificationSnooze.expires_at > datetime.now(timezone.utc),
        )
    )
    return row is not None


async def is_notification_allowed(
    session: AsyncSession,
    *,
    tg_user_id: int,
    event_type: NotificationEventType,
    auction_id: uuid.UUID | None = None,
) -> bool:
    """Backward-compatible boolean wrapper around notification_delivery_decision."""
    decision = await notification_delivery_decision(
        session,
        tg_user_id=tg_user_id,
        event_type=event_type,
        auction_id=auction_id,
    )
    return decision.allowed


async def notification_delivery_decision(
    session: AsyncSession,
    *,
    tg_user_id: int,
    event_type: NotificationEventType,
    auction_id: uuid.UUID | None = None,
) -> NotificationDeliveryDecision:
    snapshot = await load_notification_settings_by_tg_user_id(session, tg_user_id=tg_user_id)
    if snapshot is None:
        return NotificationDeliveryDecision(allowed=True, reason="allow_no_user")
    if not snapshot.master_enabled:
        return NotificationDeliveryDecision(allowed=False, reason="blocked_master")

    if auction_id is not None and event_type in _AUCTION_EVENT_TYPES:
        if await is_auction_notification_snoozed_by_tg_user_id(
            session,
            tg_user_id=tg_user_id,
            auction_id=auction_id,
        ):
            return NotificationDeliveryDecision(allowed=False, reason="blocked_auction_snooze")

    if should_defer_notification_during_quiet_hours(event_type):
        if snapshot.quiet_hours_enabled and is_within_quiet_hours(
            now_utc=datetime.now(timezone.utc),
            start_hour=snapshot.quiet_hours_start_hour,
            end_hour=snapshot.quiet_hours_end_hour,
            timezone_name=snapshot.quiet_hours_timezone,
        ):
            return NotificationDeliveryDecision(allowed=False, reason="quiet_hours_deferred")

    if event_type == NotificationEventType.AUCTION_OUTBID:
        allowed = snapshot.outbid_enabled
    elif event_type == NotificationEventType.AUCTION_FINISH:
        allowed = snapshot.auction_finish_enabled
    elif event_type == NotificationEventType.AUCTION_WIN:
        allowed = snapshot.auction_win_enabled
    elif event_type == NotificationEventType.AUCTION_MOD_ACTION:
        allowed = snapshot.auction_mod_actions_enabled
    elif event_type == NotificationEventType.POINTS:
        allowed = snapshot.points_enabled
    else:
        allowed = snapshot.support_enabled

    if allowed:
        return NotificationDeliveryDecision(allowed=True, reason="allowed")
    return NotificationDeliveryDecision(allowed=False, reason="blocked_event_toggle")
