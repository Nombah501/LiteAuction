from __future__ import annotations

import logging
import re
from enum import StrEnum

from app.infra.redis_client import redis_client
from app.services.notification_policy_service import NotificationEventType

logger = logging.getLogger(__name__)


class NotificationMetricKind(StrEnum):
    SENT = "sent"
    SUPPRESSED = "suppressed"
    AGGREGATED = "aggregated"


def _normalize_reason(reason: str) -> str:
    lowered = reason.strip().lower()
    if not lowered:
        return "unknown"
    normalized = re.sub(r"[^a-z0-9_:-]+", "_", lowered)
    return normalized.strip("_") or "unknown"


def _metric_key(*, kind: NotificationMetricKind, event_type: NotificationEventType, reason: str) -> str:
    return f"notif:metrics:{kind.value}:{event_type.value}:{reason}"


async def _record_metric(
    *,
    kind: NotificationMetricKind,
    event_type: NotificationEventType,
    reason: str,
    count: int = 1,
) -> int | None:
    safe_count = max(int(count), 1)
    normalized_reason = _normalize_reason(reason)
    key = _metric_key(kind=kind, event_type=event_type, reason=normalized_reason)

    try:
        total = await redis_client.incrby(key, safe_count)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "notification_metric_failed kind=%s event=%s reason=%s count=%s error=%s",
            kind.value,
            event_type.value,
            normalized_reason,
            safe_count,
            exc,
        )
        return None

    logger.info(
        "notification_metric kind=%s event=%s reason=%s count=%s total=%s",
        kind.value,
        event_type.value,
        normalized_reason,
        safe_count,
        total,
    )
    return int(total)


async def record_notification_sent(
    *,
    event_type: NotificationEventType,
    reason: str = "delivered",
) -> int | None:
    return await _record_metric(
        kind=NotificationMetricKind.SENT,
        event_type=event_type,
        reason=reason,
    )


async def record_notification_suppressed(
    *,
    event_type: NotificationEventType,
    reason: str,
) -> int | None:
    return await _record_metric(
        kind=NotificationMetricKind.SUPPRESSED,
        event_type=event_type,
        reason=reason,
    )


async def record_notification_aggregated(
    *,
    event_type: NotificationEventType,
    reason: str,
    count: int = 1,
) -> int | None:
    return await _record_metric(
        kind=NotificationMetricKind.AGGREGATED,
        event_type=event_type,
        reason=reason,
        count=count,
    )
