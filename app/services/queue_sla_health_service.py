from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(slots=True, frozen=True)
class QueueSlaThresholds:
    warning_window: timedelta
    critical_window: timedelta
    aging_fresh_max: timedelta
    aging_aging_max: timedelta
    aging_stale_max: timedelta


@dataclass(slots=True, frozen=True)
class QueueSlaHealthDecision:
    queue_context: str
    health_state: str
    aging_bucket: str
    reason_code: str
    countdown_seconds: int | None
    fallback_applied: bool
    fallback_notes: tuple[str, ...]


_DEFAULT_QUEUE_CONTEXT = "moderation"
_TERMINAL_STATUSES = frozenset({"RESOLVED", "REJECTED", "CLOSED", "DISMISSED", "DONE"})
_ACTIVE_STATUSES_BY_CONTEXT = {
    "moderation": frozenset({"OPEN", "IN_REVIEW"}),
    "appeals": frozenset({"OPEN", "IN_REVIEW"}),
    "risk": frozenset({"OPEN"}),
    "feedback": frozenset({"VISIBLE", "PENDING", "OPEN"}),
}

SLA_THRESHOLDS_BY_CONTEXT = {
    "moderation": QueueSlaThresholds(
        warning_window=timedelta(hours=4),
        critical_window=timedelta(hours=1),
        aging_fresh_max=timedelta(hours=2),
        aging_aging_max=timedelta(hours=6),
        aging_stale_max=timedelta(hours=12),
    ),
    "appeals": QueueSlaThresholds(
        warning_window=timedelta(hours=6),
        critical_window=timedelta(hours=2),
        aging_fresh_max=timedelta(hours=4),
        aging_aging_max=timedelta(hours=12),
        aging_stale_max=timedelta(hours=24),
    ),
    "risk": QueueSlaThresholds(
        warning_window=timedelta(hours=2),
        critical_window=timedelta(minutes=30),
        aging_fresh_max=timedelta(hours=1),
        aging_aging_max=timedelta(hours=3),
        aging_stale_max=timedelta(hours=8),
    ),
    "feedback": QueueSlaThresholds(
        warning_window=timedelta(hours=8),
        critical_window=timedelta(hours=3),
        aging_fresh_max=timedelta(hours=6),
        aging_aging_max=timedelta(hours=18),
        aging_stale_max=timedelta(hours=36),
    ),
}


def _coerce_aware_utc(value: datetime | None, *, field_name: str, fallback_notes: list[str]) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        fallback_notes.append(f"{field_name}_assumed_utc")
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def decide_queue_sla_health(
    *,
    queue_context: str,
    status: str,
    created_at: datetime | None,
    deadline_at: datetime | None,
    now: datetime | None = None,
) -> QueueSlaHealthDecision:
    fallback_notes: list[str] = []

    normalized_context = queue_context.strip().lower()
    if normalized_context not in SLA_THRESHOLDS_BY_CONTEXT:
        fallback_notes.append("unknown_queue_context")
        normalized_context = _DEFAULT_QUEUE_CONTEXT

    normalized_status = status.strip().upper()
    if not normalized_status:
        fallback_notes.append("missing_status_assumed_open")
        normalized_status = "OPEN"

    thresholds = SLA_THRESHOLDS_BY_CONTEXT[normalized_context]
    current_time = _coerce_aware_utc(now or datetime.now(UTC), field_name="now", fallback_notes=fallback_notes)
    assert current_time is not None
    created_time = _coerce_aware_utc(created_at, field_name="created_at", fallback_notes=fallback_notes)
    deadline_time = _coerce_aware_utc(deadline_at, field_name="deadline_at", fallback_notes=fallback_notes)

    if normalized_status not in _ACTIVE_STATUSES_BY_CONTEXT[normalized_context]:
        if normalized_status not in _TERMINAL_STATUSES:
            fallback_notes.append("unknown_status_assumed_active")
        else:
            return QueueSlaHealthDecision(
                queue_context=normalized_context,
                health_state="closed",
                aging_bucket="closed",
                reason_code="closed_status",
                countdown_seconds=None,
                fallback_applied=bool(fallback_notes),
                fallback_notes=tuple(fallback_notes),
            )

    if created_time is None:
        aging_bucket = "unknown"
        fallback_notes.append("missing_created_at")
    else:
        age = current_time - created_time
        if age.total_seconds() < 0:
            fallback_notes.append("created_at_in_future")
            age = timedelta(0)

        if age <= thresholds.aging_fresh_max:
            aging_bucket = "fresh"
        elif age <= thresholds.aging_aging_max:
            aging_bucket = "aging"
        elif age <= thresholds.aging_stale_max:
            aging_bucket = "stale"
        else:
            aging_bucket = "critical"

    if deadline_time is None:
        return QueueSlaHealthDecision(
            queue_context=normalized_context,
            health_state="no_sla",
            aging_bucket=aging_bucket,
            reason_code="missing_deadline",
            countdown_seconds=None,
            fallback_applied=bool(fallback_notes),
            fallback_notes=tuple(fallback_notes),
        )

    if created_time is not None and deadline_time < created_time:
        fallback_notes.append("deadline_before_created_at")

    remaining = deadline_time - current_time
    countdown_seconds = max(int(remaining.total_seconds()), 0)

    if remaining <= timedelta(0):
        return QueueSlaHealthDecision(
            queue_context=normalized_context,
            health_state="overdue",
            aging_bucket="overdue",
            reason_code="deadline_elapsed",
            countdown_seconds=0,
            fallback_applied=bool(fallback_notes),
            fallback_notes=tuple(fallback_notes),
        )

    if remaining <= thresholds.critical_window:
        health_state = "critical"
        reason_code = "critical_window"
    elif remaining <= thresholds.warning_window:
        health_state = "warning"
        reason_code = "warning_window"
    else:
        health_state = "healthy"
        reason_code = "within_budget"

    return QueueSlaHealthDecision(
        queue_context=normalized_context,
        health_state=health_state,
        aging_bucket=aging_bucket,
        reason_code=reason_code,
        countdown_seconds=countdown_seconds,
        fallback_applied=bool(fallback_notes),
        fallback_notes=tuple(fallback_notes),
    )
