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
    window_start = now - timedelta(hours=max(int(lookback_hours), 1))

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
        .where(AdminQueuePresetTelemetryEvent.created_at >= window_start)
        .group_by(
            AdminQueuePresetTelemetryEvent.queue_context,
            AdminQueuePresetTelemetryEvent.queue_key,
            AdminQueuePresetTelemetryEvent.preset_id,
        )
        .order_by(
            func.count(AdminQueuePresetTelemetryEvent.id).desc(),
            AdminQueuePresetTelemetryEvent.queue_key.asc(),
            AdminQueuePresetTelemetryEvent.preset_id.asc().nullsfirst(),
        )
    )

    if queue_context is not None:
        stmt = stmt.where(AdminQueuePresetTelemetryEvent.queue_context == _normalize_queue_context(queue_context))

    rows = (await session.execute(stmt)).all()
    segments: list[dict[str, Any]] = []
    for row in rows:
        events_total = int(row.events_total or 0)
        reopen_total = int(row.reopen_total or 0)
        reopen_rate = float(reopen_total / events_total) if events_total > 0 else 0.0
        segments.append(
            {
                "queue_context": str(row.queue_context),
                "queue_key": str(row.queue_key),
                "preset_id": int(row.preset_id) if row.preset_id is not None else None,
                "events_total": events_total,
                "avg_time_to_action_ms": float(row.avg_time_to_action_ms) if row.avg_time_to_action_ms is not None else None,
                "avg_filter_churn_count": float(row.avg_filter_churn_count) if row.avg_filter_churn_count is not None else 0.0,
                "reopen_total": reopen_total,
                "reopen_rate": reopen_rate,
            }
        )
    return segments
