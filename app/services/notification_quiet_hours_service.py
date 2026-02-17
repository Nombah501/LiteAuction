from __future__ import annotations

import logging

from app.infra.redis_client import redis_client
from app.services.notification_policy_service import NotificationEventType

logger = logging.getLogger(__name__)


def _quiet_hours_deferred_key(*, tg_user_id: int, event_type: NotificationEventType) -> str:
    return f"notif:quiet:deferred:{tg_user_id}:{event_type.value}"


async def defer_notification_event(
    *,
    tg_user_id: int,
    event_type: NotificationEventType,
) -> int:
    key = _quiet_hours_deferred_key(tg_user_id=tg_user_id, event_type=event_type)
    try:
        deferred_count = int(await redis_client.incr(key))
        await redis_client.expire(key, 172800)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "quiet_hours_defer_failed tg_user_id=%s event=%s error=%s",
            tg_user_id,
            event_type.value,
            exc,
        )
        return 0
    return deferred_count


async def pop_deferred_notification_count(
    *,
    tg_user_id: int,
    event_type: NotificationEventType,
) -> int:
    key = _quiet_hours_deferred_key(tg_user_id=tg_user_id, event_type=event_type)
    try:
        value = await redis_client.getdel(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "quiet_hours_pop_failed tg_user_id=%s event=%s error=%s",
            tg_user_id,
            event_type.value,
            exc,
        )
        return 0
    if value is None:
        return 0
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0
