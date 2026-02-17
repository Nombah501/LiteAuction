from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from enum import StrEnum

from app.infra.redis_client import redis_client
from app.services.notification_policy_service import NotificationEventType

logger = logging.getLogger(__name__)
_METRIC_SCAN_MATCH = "notif:metrics:*"


@dataclass(slots=True, frozen=True)
class NotificationMetricBucket:
    event_type: NotificationEventType
    reason: str
    total: int


@dataclass(slots=True, frozen=True)
class NotificationMetricsSnapshot:
    sent_total: int
    suppressed_total: int
    aggregated_total: int
    top_suppressed: tuple[NotificationMetricBucket, ...]


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


def _parse_metric_key(key: str) -> tuple[NotificationMetricKind, NotificationEventType, str] | None:
    parts = key.split(":", 4)
    if len(parts) != 5:
        return None
    if parts[0] != "notif" or parts[1] != "metrics":
        return None

    try:
        kind = NotificationMetricKind(parts[2])
        event_type = NotificationEventType(parts[3])
    except ValueError:
        return None

    reason = _normalize_reason(parts[4])
    return kind, event_type, reason


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


async def load_notification_metrics_snapshot(*, top_limit: int = 5) -> NotificationMetricsSnapshot:
    cursor: int | str = 0
    totals: dict[NotificationMetricKind, int] = {
        NotificationMetricKind.SENT: 0,
        NotificationMetricKind.SUPPRESSED: 0,
        NotificationMetricKind.AGGREGATED: 0,
    }
    suppressed_groups: dict[tuple[NotificationEventType, str], int] = {}

    try:
        while True:
            cursor, keys = await redis_client.scan(
                cursor=cursor,
                match=_METRIC_SCAN_MATCH,
                count=200,
            )
            if keys:
                raw_values = await redis_client.mget(keys)
                for key, raw_value in zip(keys, raw_values, strict=False):
                    parsed = _parse_metric_key(str(key))
                    if parsed is None or raw_value is None:
                        continue

                    try:
                        value = max(int(raw_value), 0)
                    except (TypeError, ValueError):
                        continue

                    kind, event_type, reason = parsed
                    totals[kind] += value
                    if kind == NotificationMetricKind.SUPPRESSED:
                        group_key = (event_type, reason)
                        suppressed_groups[group_key] = suppressed_groups.get(group_key, 0) + value

            if int(cursor) == 0:
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning("notification_metrics_snapshot_failed error=%s", exc)

    normalized_top_limit = max(int(top_limit), 1)
    top_suppressed = tuple(
        NotificationMetricBucket(event_type=event_type, reason=reason, total=total)
        for (event_type, reason), total in sorted(
            suppressed_groups.items(),
            key=lambda item: (-item[1], item[0][0].value, item[0][1]),
        )[:normalized_top_limit]
    )

    return NotificationMetricsSnapshot(
        sent_total=totals[NotificationMetricKind.SENT],
        suppressed_total=totals[NotificationMetricKind.SUPPRESSED],
        aggregated_total=totals[NotificationMetricKind.AGGREGATED],
        top_suppressed=top_suppressed,
    )
