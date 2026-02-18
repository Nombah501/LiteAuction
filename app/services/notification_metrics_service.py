from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import re
from enum import StrEnum

from app.infra.redis_client import redis_client
from app.services.notification_policy_service import NotificationEventType

logger = logging.getLogger(__name__)
_METRIC_SCAN_MATCH = "notif:metrics:*"
_METRIC_HOURLY_RETENTION_HOURS = 10 * 24
_METRIC_HOURLY_RETENTION_SECONDS = _METRIC_HOURLY_RETENTION_HOURS * 3600
_SUPPRESSED_DELTA_WARNING_THRESHOLD = 30
_SUPPRESSED_DELTA_HIGH_THRESHOLD = 80
_SUPPRESSED_DELTA_CRITICAL_THRESHOLD = 150
_TOP_SUPPRESSION_SHARE_WARNING_THRESHOLD = 0.35
_FORBIDDEN_BAD_REQUEST_SHARE_HIGH_THRESHOLD = 0.25
_SENT_DELTA_CRITICAL_DROP_THRESHOLD = -80


@dataclass(slots=True, frozen=True)
class NotificationMetricTotals:
    sent_total: int
    suppressed_total: int
    aggregated_total: int


@dataclass(slots=True, frozen=True)
class NotificationMetricDelta:
    sent_delta: int
    suppressed_delta: int
    aggregated_delta: int


class NotificationAlertSeverity(StrEnum):
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


class NotificationAlertCode(StrEnum):
    SUPPRESSED_DELTA_WARNING = "suppressed_delta_warning"
    SUPPRESSED_DELTA_HIGH = "suppressed_delta_high"
    SUPPRESSED_DELTA_CRITICAL = "suppressed_delta_critical"
    TOP_SUPPRESSION_SHARE_WARNING = "top_suppression_share_warning"
    FORBIDDEN_BAD_REQUEST_SHARE_HIGH = "forbidden_bad_request_share_high"
    SENT_DROP_WITH_SUPPRESSION_CRITICAL = "sent_drop_with_suppression_critical"


@dataclass(slots=True, frozen=True)
class NotificationAlertHint:
    severity: NotificationAlertSeverity
    code: NotificationAlertCode
    message: str


@dataclass(slots=True, frozen=True)
class NotificationMetricBucket:
    event_type: NotificationEventType
    reason: str
    total: int


@dataclass(slots=True, frozen=True)
class NotificationMetricsSnapshot:
    all_time: NotificationMetricTotals
    last_24h: NotificationMetricTotals
    previous_24h: NotificationMetricTotals
    delta_24h_vs_previous_24h: NotificationMetricDelta
    last_7d: NotificationMetricTotals
    top_suppressed: tuple[NotificationMetricBucket, ...]
    top_suppressed_24h: tuple[NotificationMetricBucket, ...]
    top_suppressed_7d: tuple[NotificationMetricBucket, ...]
    alert_hints: tuple[NotificationAlertHint, ...]


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


def _hour_bucket(now_utc: datetime) -> str:
    return now_utc.strftime("%Y%m%d%H")


def _hourly_metric_key(
    *,
    hour_bucket: str,
    kind: NotificationMetricKind,
    event_type: NotificationEventType,
    reason: str,
) -> str:
    return f"notif:metrics:h:{hour_bucket}:{kind.value}:{event_type.value}:{reason}"


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


def _parse_hourly_metric_key(key: str) -> tuple[str, NotificationMetricKind, NotificationEventType, str] | None:
    parts = key.split(":", 6)
    if len(parts) != 7:
        return None
    if parts[0] != "notif" or parts[1] != "metrics" or parts[2] != "h":
        return None

    hour_bucket = parts[3]
    if len(hour_bucket) != 10 or not hour_bucket.isdigit():
        return None

    try:
        kind = NotificationMetricKind(parts[4])
        event_type = NotificationEventType(parts[5])
    except ValueError:
        return None

    reason = _normalize_reason(parts[6])
    return hour_bucket, kind, event_type, reason


def _totals_from_map(totals: dict[NotificationMetricKind, int]) -> NotificationMetricTotals:
    return NotificationMetricTotals(
        sent_total=totals[NotificationMetricKind.SENT],
        suppressed_total=totals[NotificationMetricKind.SUPPRESSED],
        aggregated_total=totals[NotificationMetricKind.AGGREGATED],
    )


def _empty_totals_map() -> dict[NotificationMetricKind, int]:
    return {
        NotificationMetricKind.SENT: 0,
        NotificationMetricKind.SUPPRESSED: 0,
        NotificationMetricKind.AGGREGATED: 0,
    }


def _window_hour_buckets(
    *,
    now_utc: datetime,
    start_offset_hours: int,
    duration_hours: int,
) -> tuple[str, ...]:
    normalized_start = max(int(start_offset_hours), 0)
    normalized_duration = max(int(duration_hours), 1)
    return tuple(
        _hour_bucket(now_utc - timedelta(hours=normalized_start + offset))
        for offset in range(normalized_duration)
    )


def _matches_metric_filters(
    *,
    event_type: NotificationEventType,
    reason: str,
    event_type_filter: NotificationEventType | None,
    reason_filter: str | None,
) -> bool:
    if event_type_filter is not None and event_type != event_type_filter:
        return False
    if reason_filter is not None and reason_filter not in reason:
        return False
    return True


def _totals_delta(*, current: NotificationMetricTotals, previous: NotificationMetricTotals) -> NotificationMetricDelta:
    return NotificationMetricDelta(
        sent_delta=current.sent_total - previous.sent_total,
        suppressed_delta=current.suppressed_total - previous.suppressed_total,
        aggregated_delta=current.aggregated_total - previous.aggregated_total,
    )


def _top_suppressed_from_groups(
    *,
    suppressed_groups: dict[tuple[NotificationEventType, str], int],
    top_limit: int,
) -> tuple[NotificationMetricBucket, ...]:
    normalized_top_limit = max(int(top_limit), 1)
    return tuple(
        NotificationMetricBucket(event_type=event_type, reason=reason, total=total)
        for (event_type, reason), total in sorted(
            suppressed_groups.items(),
            key=lambda item: (-item[1], item[0][0].value, item[0][1]),
        )[:normalized_top_limit]
    )


def _notification_bucket_label(item: NotificationMetricBucket) -> str:
    return f"{item.event_type.value}/{item.reason}"


def _format_signed_int(value: int) -> str:
    if value > 0:
        return f"+{value}"
    return str(value)


def _build_alert_hints(
    *,
    last_24h: NotificationMetricTotals,
    delta_24h_vs_previous_24h: NotificationMetricDelta,
    top_suppressed_24h: tuple[NotificationMetricBucket, ...],
    forbidden_bad_request_suppressed_24h_total: int,
) -> tuple[NotificationAlertHint, ...]:
    hints: list[NotificationAlertHint] = []
    suppressed_delta = delta_24h_vs_previous_24h.suppressed_delta

    if suppressed_delta >= _SUPPRESSED_DELTA_CRITICAL_THRESHOLD:
        hints.append(
            NotificationAlertHint(
                severity=NotificationAlertSeverity.CRITICAL,
                code=NotificationAlertCode.SUPPRESSED_DELTA_CRITICAL,
                message=(
                    f"suppressed delta {_format_signed_int(suppressed_delta)} "
                    f">= +{_SUPPRESSED_DELTA_CRITICAL_THRESHOLD}"
                ),
            )
        )
    elif suppressed_delta >= _SUPPRESSED_DELTA_HIGH_THRESHOLD:
        hints.append(
            NotificationAlertHint(
                severity=NotificationAlertSeverity.HIGH,
                code=NotificationAlertCode.SUPPRESSED_DELTA_HIGH,
                message=(
                    f"suppressed delta {_format_signed_int(suppressed_delta)} "
                    f">= +{_SUPPRESSED_DELTA_HIGH_THRESHOLD}"
                ),
            )
        )
    elif suppressed_delta >= _SUPPRESSED_DELTA_WARNING_THRESHOLD:
        hints.append(
            NotificationAlertHint(
                severity=NotificationAlertSeverity.WARNING,
                code=NotificationAlertCode.SUPPRESSED_DELTA_WARNING,
                message=(
                    f"suppressed delta {_format_signed_int(suppressed_delta)} "
                    f">= +{_SUPPRESSED_DELTA_WARNING_THRESHOLD}"
                ),
            )
        )

    if (
        delta_24h_vs_previous_24h.sent_delta <= _SENT_DELTA_CRITICAL_DROP_THRESHOLD
        and suppressed_delta > 0
    ):
        hints.append(
            NotificationAlertHint(
                severity=NotificationAlertSeverity.CRITICAL,
                code=NotificationAlertCode.SENT_DROP_WITH_SUPPRESSION_CRITICAL,
                message=(
                    f"sent delta {_format_signed_int(delta_24h_vs_previous_24h.sent_delta)} <= "
                    f"{_SENT_DELTA_CRITICAL_DROP_THRESHOLD} with suppression growth"
                ),
            )
        )

    suppressed_total = last_24h.suppressed_total
    if suppressed_total > 0 and top_suppressed_24h:
        top_item = top_suppressed_24h[0]
        top_share = top_item.total / suppressed_total
        if top_share >= _TOP_SUPPRESSION_SHARE_WARNING_THRESHOLD:
            hints.append(
                NotificationAlertHint(
                    severity=NotificationAlertSeverity.WARNING,
                    code=NotificationAlertCode.TOP_SUPPRESSION_SHARE_WARNING,
                    message=(
                        f"top 24h suppression share {top_share:.0%} "
                        f"({_notification_bucket_label(top_item)} / {top_item.total} of {suppressed_total})"
                    ),
                )
            )

        forbidden_bad_request_share = forbidden_bad_request_suppressed_24h_total / suppressed_total
        if forbidden_bad_request_share >= _FORBIDDEN_BAD_REQUEST_SHARE_HIGH_THRESHOLD:
            hints.append(
                NotificationAlertHint(
                    severity=NotificationAlertSeverity.HIGH,
                    code=NotificationAlertCode.FORBIDDEN_BAD_REQUEST_SHARE_HIGH,
                    message=(
                        f"forbidden+bad_request share {forbidden_bad_request_share:.0%} "
                        f"({forbidden_bad_request_suppressed_24h_total} of {suppressed_total})"
                    ),
                )
            )

    return tuple(hints)


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

    hour_bucket = _hour_bucket(datetime.now(timezone.utc))
    hourly_key = _hourly_metric_key(
        hour_bucket=hour_bucket,
        kind=kind,
        event_type=event_type,
        reason=normalized_reason,
    )
    try:
        await redis_client.incrby(hourly_key, safe_count)
        await redis_client.expire(hourly_key, _METRIC_HOURLY_RETENTION_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "notification_hourly_metric_failed kind=%s event=%s reason=%s count=%s error=%s",
            kind.value,
            event_type.value,
            normalized_reason,
            safe_count,
            exc,
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


async def _load_all_time_totals_and_top(
    *,
    top_limit: int,
    event_type_filter: NotificationEventType | None,
    reason_filter: str | None,
) -> tuple[dict[NotificationMetricKind, int], tuple[NotificationMetricBucket, ...]]:
    cursor = 0
    totals = _empty_totals_map()
    suppressed_groups: dict[tuple[NotificationEventType, str], int] = {}

    try:
        while True:
            next_cursor, keys = await redis_client.scan(
                cursor=cursor,
                match=_METRIC_SCAN_MATCH,
                count=200,
            )
            cursor = int(next_cursor)
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
                    if not _matches_metric_filters(
                        event_type=event_type,
                        reason=reason,
                        event_type_filter=event_type_filter,
                        reason_filter=reason_filter,
                    ):
                        continue

                    totals[kind] += value
                    if kind == NotificationMetricKind.SUPPRESSED:
                        group_key = (event_type, reason)
                        suppressed_groups[group_key] = suppressed_groups.get(group_key, 0) + value

            if cursor == 0:
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning("notification_metrics_snapshot_failed error=%s", exc)

    top_suppressed = _top_suppressed_from_groups(
        suppressed_groups=suppressed_groups,
        top_limit=top_limit,
    )

    return totals, top_suppressed


async def _load_recent_window_totals(
    *,
    now_utc: datetime,
    start_offset_hours: int,
    duration_hours: int,
    top_limit: int,
    event_type_filter: NotificationEventType | None,
    reason_filter: str | None,
) -> tuple[dict[NotificationMetricKind, int], tuple[NotificationMetricBucket, ...], int]:
    buckets = set(
        _window_hour_buckets(
            now_utc=now_utc,
            start_offset_hours=start_offset_hours,
            duration_hours=duration_hours,
        )
    )
    totals = _empty_totals_map()
    suppressed_groups: dict[tuple[NotificationEventType, str], int] = {}
    forbidden_bad_request_suppressed_total = 0

    try:
        for hour_bucket in buckets:
            cursor = 0
            match_pattern = f"notif:metrics:h:{hour_bucket}:*"
            while True:
                next_cursor, keys = await redis_client.scan(
                    cursor=int(cursor),
                    match=match_pattern,
                    count=200,
                )
                cursor = int(next_cursor)
                if keys:
                    raw_values = await redis_client.mget(keys)
                    for key, raw_value in zip(keys, raw_values, strict=False):
                        parsed = _parse_hourly_metric_key(str(key))
                        if parsed is None or raw_value is None:
                            continue
                        parsed_hour_bucket, kind, _event_type, _reason = parsed
                        event_type = _event_type
                        reason = _reason
                        if parsed_hour_bucket not in buckets:
                            continue
                        if not _matches_metric_filters(
                            event_type=event_type,
                            reason=reason,
                            event_type_filter=event_type_filter,
                            reason_filter=reason_filter,
                        ):
                            continue

                        try:
                            value = max(int(raw_value), 0)
                        except (TypeError, ValueError):
                            continue

                        totals[kind] += value
                        if kind == NotificationMetricKind.SUPPRESSED:
                            group_key = (event_type, reason)
                            suppressed_groups[group_key] = suppressed_groups.get(group_key, 0) + value
                            if reason in {"forbidden", "bad_request"}:
                                forbidden_bad_request_suppressed_total += value

                if cursor == 0:
                    break
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "notification_metrics_window_snapshot_failed start_offset_hours=%s duration_hours=%s error=%s",
            start_offset_hours,
            duration_hours,
            exc,
        )

    return (
        totals,
        _top_suppressed_from_groups(suppressed_groups=suppressed_groups, top_limit=top_limit),
        forbidden_bad_request_suppressed_total,
    )


async def load_notification_metrics_snapshot(
    *,
    top_limit: int = 5,
    now_utc: datetime | None = None,
    event_type_filter: NotificationEventType | None = None,
    reason_filter: str | None = None,
) -> NotificationMetricsSnapshot:
    effective_now_utc = now_utc or datetime.now(timezone.utc)
    if effective_now_utc.tzinfo is None:
        effective_now_utc = effective_now_utc.replace(tzinfo=timezone.utc)

    normalized_reason_filter = None
    if reason_filter is not None:
        normalized = _normalize_reason(reason_filter)
        if normalized and normalized != "unknown":
            normalized_reason_filter = normalized

    all_time_totals_map, top_suppressed = await _load_all_time_totals_and_top(
        top_limit=top_limit,
        event_type_filter=event_type_filter,
        reason_filter=normalized_reason_filter,
    )
    last_24h_totals_map = await _load_recent_window_totals(
        now_utc=effective_now_utc,
        start_offset_hours=0,
        duration_hours=24,
        top_limit=top_limit,
        event_type_filter=event_type_filter,
        reason_filter=normalized_reason_filter,
    )
    previous_24h_totals_map = await _load_recent_window_totals(
        now_utc=effective_now_utc,
        start_offset_hours=24,
        duration_hours=24,
        top_limit=top_limit,
        event_type_filter=event_type_filter,
        reason_filter=normalized_reason_filter,
    )
    last_7d_totals_map = await _load_recent_window_totals(
        now_utc=effective_now_utc,
        start_offset_hours=0,
        duration_hours=24 * 7,
        top_limit=top_limit,
        event_type_filter=event_type_filter,
        reason_filter=normalized_reason_filter,
    )

    (
        last_24h_totals_values,
        top_suppressed_24h,
        forbidden_bad_request_suppressed_24h_total,
    ) = last_24h_totals_map
    previous_24h_totals_values, _, _ = previous_24h_totals_map
    last_7d_totals_values, top_suppressed_7d, _ = last_7d_totals_map

    last_24h_totals = _totals_from_map(last_24h_totals_values)
    previous_24h_totals = _totals_from_map(previous_24h_totals_values)
    delta_24h_vs_previous_24h = _totals_delta(
        current=last_24h_totals,
        previous=previous_24h_totals,
    )

    return NotificationMetricsSnapshot(
        all_time=_totals_from_map(all_time_totals_map),
        last_24h=last_24h_totals,
        previous_24h=previous_24h_totals,
        delta_24h_vs_previous_24h=delta_24h_vs_previous_24h,
        last_7d=_totals_from_map(last_7d_totals_values),
        top_suppressed=top_suppressed,
        top_suppressed_24h=top_suppressed_24h,
        top_suppressed_7d=top_suppressed_7d,
        alert_hints=_build_alert_hints(
            last_24h=last_24h_totals,
            delta_24h_vs_previous_24h=delta_24h_vs_previous_24h,
            top_suppressed_24h=top_suppressed_24h,
            forbidden_bad_request_suppressed_24h_total=forbidden_bad_request_suppressed_24h_total,
        ),
    )
