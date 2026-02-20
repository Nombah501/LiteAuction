from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminQueuePresetTelemetryEvent
from app.services.admin_list_preferences_service import _normalize_subject_key
from app.web.auth import AdminAuthContext

QUEUE_CONTEXT_TO_QUEUE_KEY = {
    "moderation": "complaints",
    "appeals": "appeals",
    "risk": "signals",
    "feedback": "trade_feedback",
}
_ALLOWED_ACTIONS = frozenset({"save", "update", "select", "delete", "set_default"})
_TREND_MIN_SAMPLE_SIZE = 5


def _normalize_queue_context(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in QUEUE_CONTEXT_TO_QUEUE_KEY:
        raise ValueError("Unknown queue context")
    return normalized


def _normalize_action(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in _ALLOWED_ACTIONS:
        raise ValueError("Unknown workflow preset action")
    return normalized


def _normalize_optional_non_negative_int(
    value: Any,
    *,
    maximum: int,
) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return min(parsed, maximum)


def _normalize_non_negative_int(value: Any, *, maximum: int, default: int = 0) -> int:
    parsed = _normalize_optional_non_negative_int(value, maximum=maximum)
    if parsed is None:
        return default
    return parsed


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _normalize_preset_id(value: Any) -> int | None:
    parsed = _normalize_optional_non_negative_int(value, maximum=2_147_483_647)
    if parsed is None or parsed <= 0:
        return None
    return parsed


async def record_workflow_preset_telemetry_event(
    session: AsyncSession,
    *,
    auth: AdminAuthContext,
    queue_context: str,
    action: str,
    preset_id: Any = None,
    time_to_action_ms: Any = None,
    reopen_signal: Any = None,
    filter_churn_count: Any = None,
    admin_token: str | None = None,
) -> None:
    normalized_context = _normalize_queue_context(queue_context)
    normalized_action = _normalize_action(action)
    subject_key = _normalize_subject_key(auth=auth, admin_token=admin_token)

    event = AdminQueuePresetTelemetryEvent(
        queue_context=normalized_context,
        queue_key=QUEUE_CONTEXT_TO_QUEUE_KEY[normalized_context],
        preset_id=_normalize_preset_id(preset_id),
        action=normalized_action,
        actor_subject_key=subject_key,
        time_to_action_ms=_normalize_optional_non_negative_int(time_to_action_ms, maximum=86_400_000),
        reopen_signal=_normalize_bool(reopen_signal),
        filter_churn_count=_normalize_non_negative_int(filter_churn_count, maximum=1000, default=0),
    )
    session.add(event)


async def load_workflow_preset_telemetry_segments(
    session: AsyncSession,
    *,
    queue_context: str | None = None,
    lookback_hours: int = 24 * 7,
) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    normalized_lookback_hours = max(int(lookback_hours), 1)
    current_window_start = now - timedelta(hours=normalized_lookback_hours)
    previous_window_start = current_window_start - timedelta(hours=normalized_lookback_hours)
    normalized_context = _normalize_queue_context(queue_context) if queue_context is not None else None

    async def _load_window(*, start: datetime, end: datetime) -> dict[tuple[str, str, int | None], dict[str, float | int | None]]:
        stmt = (
            select(
                AdminQueuePresetTelemetryEvent.queue_context,
                AdminQueuePresetTelemetryEvent.queue_key,
                AdminQueuePresetTelemetryEvent.preset_id,
                func.count(AdminQueuePresetTelemetryEvent.id).label("events_total"),
                func.avg(AdminQueuePresetTelemetryEvent.time_to_action_ms).label("avg_time_to_action_ms"),
                func.avg(AdminQueuePresetTelemetryEvent.filter_churn_count).label("avg_filter_churn_count"),
                func.sum(
                    case(
                        (AdminQueuePresetTelemetryEvent.reopen_signal.is_(True), 1),
                        else_=0,
                    )
                ).label("reopen_total"),
            )
            .where(
                AdminQueuePresetTelemetryEvent.created_at >= start,
                AdminQueuePresetTelemetryEvent.created_at < end,
            )
            .group_by(
                AdminQueuePresetTelemetryEvent.queue_context,
                AdminQueuePresetTelemetryEvent.queue_key,
                AdminQueuePresetTelemetryEvent.preset_id,
            )
        )
        if normalized_context is not None:
            stmt = stmt.where(AdminQueuePresetTelemetryEvent.queue_context == normalized_context)

        rows = (await session.execute(stmt)).all()
        window: dict[tuple[str, str, int | None], dict[str, float | int | None]] = {}
        for row in rows:
            key = (
                str(row.queue_context),
                str(row.queue_key),
                int(row.preset_id) if row.preset_id is not None else None,
            )
            window[key] = {
                "events_total": int(row.events_total or 0),
                "avg_time_to_action_ms": (
                    float(row.avg_time_to_action_ms)
                    if row.avg_time_to_action_ms is not None
                    else None
                ),
                "avg_filter_churn_count": (
                    float(row.avg_filter_churn_count)
                    if row.avg_filter_churn_count is not None
                    else 0.0
                ),
                "reopen_total": int(row.reopen_total or 0),
            }
        return window

    current_window = await _load_window(start=current_window_start, end=now)
    previous_window = await _load_window(start=previous_window_start, end=current_window_start)

    ordered_keys = sorted(
        current_window.keys(),
        key=lambda key: (
            -int(current_window[key]["events_total"] or 0),
            key[1],
            key[2] if key[2] is not None else -1,
        ),
    )

    segments: list[dict[str, Any]] = []
    for queue_context_value, queue_key_value, preset_id_value in ordered_keys:
        current = current_window[(queue_context_value, queue_key_value, preset_id_value)]
        previous = previous_window.get(
            (queue_context_value, queue_key_value, preset_id_value),
            {
                "events_total": 0,
                "avg_time_to_action_ms": None,
                "avg_filter_churn_count": 0.0,
                "reopen_total": 0,
            },
        )
        events_total = int(current["events_total"] or 0)
        reopen_total = int(current["reopen_total"] or 0)
        reopen_rate = float(reopen_total / events_total) if events_total > 0 else 0.0

        previous_events_total = int(previous["events_total"] or 0)
        previous_reopen_total = int(previous["reopen_total"] or 0)
        previous_reopen_rate = (
            float(previous_reopen_total / previous_events_total)
            if previous_events_total > 0
            else 0.0
        )

        current_avg_time = (
            float(current["avg_time_to_action_ms"])
            if current["avg_time_to_action_ms"] is not None
            else None
        )
        previous_avg_time = (
            float(previous["avg_time_to_action_ms"])
            if previous["avg_time_to_action_ms"] is not None
            else None
        )
        current_avg_churn = float(current["avg_filter_churn_count"] or 0.0)
        previous_avg_churn = float(previous["avg_filter_churn_count"] or 0.0)

        low_sample_guardrail = (
            events_total < _TREND_MIN_SAMPLE_SIZE
            or previous_events_total < _TREND_MIN_SAMPLE_SIZE
        )

        if low_sample_guardrail:
            time_to_action_delta_ms = None
            reopen_rate_delta = None
            filter_churn_delta = None
        else:
            time_to_action_delta_ms = (
                current_avg_time - previous_avg_time
                if current_avg_time is not None and previous_avg_time is not None
                else None
            )
            reopen_rate_delta = reopen_rate - previous_reopen_rate
            filter_churn_delta = current_avg_churn - previous_avg_churn

        segments.append(
            {
                "queue_context": queue_context_value,
                "queue_key": queue_key_value,
                "preset_id": preset_id_value,
                "events_total": events_total,
                "avg_time_to_action_ms": current_avg_time,
                "avg_filter_churn_count": current_avg_churn,
                "reopen_total": reopen_total,
                "reopen_rate": reopen_rate,
                "trend_window_hours": normalized_lookback_hours,
                "trend_min_sample_size": _TREND_MIN_SAMPLE_SIZE,
                "trend_previous_events_total": previous_events_total,
                "trend_low_sample_guardrail": low_sample_guardrail,
                "time_to_action_delta_ms": time_to_action_delta_ms,
                "reopen_rate_delta": reopen_rate_delta,
                "filter_churn_delta": filter_churn_delta,
            }
        )
    return segments
