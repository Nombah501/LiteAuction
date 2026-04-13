from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

from app.db.enums import AppealSourceType, AppealStatus, PointsEventType
from app.db.models import Appeal
from app.web.components import _fmt_ts


def _parse_appeal_status_filter(raw: str) -> AppealStatus | None:
    value = raw.strip().lower()
    if value == "all":
        return None
    if value == "open":
        return AppealStatus.OPEN
    if value == "in_review":
        return AppealStatus.IN_REVIEW
    if value == "resolved":
        return AppealStatus.RESOLVED
    if value == "rejected":
        return AppealStatus.REJECTED
    raise HTTPException(status_code=400, detail="Invalid appeals status filter")


def _parse_appeal_source_filter(raw: str) -> AppealSourceType | None:
    value = raw.strip().lower()
    if value == "all":
        return None
    if value == "complaint":
        return AppealSourceType.COMPLAINT
    if value == "risk":
        return AppealSourceType.RISK
    if value == "manual":
        return AppealSourceType.MANUAL
    raise HTTPException(status_code=400, detail="Invalid appeals source filter")


def _parse_appeal_overdue_filter(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"all", "only", "none"}:
        return value
    raise HTTPException(status_code=400, detail="Invalid appeals overdue filter")


def _parse_appeal_escalated_filter(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"all", "only", "none"}:
        return value
    raise HTTPException(status_code=400, detail="Invalid appeals escalated filter")


def _parse_appeal_sla_health_filter(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"all", "healthy", "warning", "critical", "overdue", "no_sla"}:
        return value
    raise HTTPException(status_code=400, detail="Invalid appeals SLA health filter")


def _parse_appeal_aging_bucket_filter(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"all", "fresh", "aging", "stale", "critical", "overdue", "unknown"}:
        return value
    raise HTTPException(status_code=400, detail="Invalid appeals aging filter")


def _parse_complaint_sla_health_filter(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"all", "healthy", "warning", "critical", "overdue", "no_sla"}:
        return value
    raise HTTPException(status_code=400, detail="Invalid complaints SLA health filter")


def _parse_complaint_aging_bucket_filter(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"all", "fresh", "aging", "stale", "critical", "overdue", "unknown"}:
        return value
    raise HTTPException(status_code=400, detail="Invalid complaints aging filter")


def _parse_signal_sla_health_filter(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"all", "healthy", "warning", "critical", "overdue", "no_sla"}:
        return value
    raise HTTPException(status_code=400, detail="Invalid signals SLA health filter")


def _parse_signal_aging_bucket_filter(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"all", "fresh", "aging", "stale", "critical", "overdue", "unknown"}:
        return value
    raise HTTPException(status_code=400, detail="Invalid signals aging filter")


def _parse_trade_feedback_status(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"all", "visible", "hidden"}:
        return value
    raise HTTPException(status_code=400, detail="Invalid trade feedback status filter")


def _parse_trade_feedback_moderated_filter(raw: str) -> str:
    value = raw.strip().lower()
    if value in {"all", "only", "none"}:
        return value
    raise HTTPException(status_code=400, detail="Invalid trade feedback moderated filter")


def _parse_trade_feedback_min_rating(raw: str) -> int | None:
    value = raw.strip()
    if not value:
        return None
    parsed = _parse_non_negative_int(value)
    if parsed is None or parsed < 1 or parsed > 5:
        raise HTTPException(status_code=400, detail="Invalid trade feedback rating filter")
    return parsed


def _parse_optional_tg_user_id(raw: str) -> int | None:
    value = raw.strip()
    if not value:
        return None
    parsed = _parse_non_negative_int(value)
    if parsed is None or parsed <= 0:
        raise HTTPException(status_code=400, detail="Invalid trade feedback tg user filter")
    return parsed


def _appeal_source_label(source_type: AppealSourceType, source_id: int | None) -> str:
    if source_type == AppealSourceType.COMPLAINT:
        return f"Жалоба #{source_id}" if source_id is not None else "Жалоба"
    if source_type == AppealSourceType.RISK:
        return f"Фрод-сигнал #{source_id}" if source_id is not None else "Фрод-сигнал"
    return "Ручная"


def _appeal_status_label(status: AppealStatus | str) -> str:
    raw = status.value if isinstance(status, AppealStatus) else str(status)
    normalized = raw.strip().upper()
    if normalized == AppealStatus.OPEN.value:
        return "Открыта"
    if normalized == AppealStatus.IN_REVIEW.value:
        return "На рассмотрении"
    if normalized == AppealStatus.RESOLVED.value:
        return "Удовлетворена"
    if normalized == AppealStatus.REJECTED.value:
        return "Отклонена"
    return raw


def _appeal_is_overdue(appeal: Appeal, *, now: datetime | None = None) -> bool:
    status = AppealStatus(appeal.status)
    if status not in {AppealStatus.OPEN, AppealStatus.IN_REVIEW}:
        return False
    if appeal.sla_deadline_at is None:
        return False
    current_time = now or datetime.now(UTC)
    return appeal.sla_deadline_at <= current_time


def _format_duration_compact(delta: timedelta) -> str:
    total_seconds = max(int(delta.total_seconds()), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    if hours > 0:
        return f"{hours}ч {minutes}м"
    return f"{minutes}м"


def _appeal_sla_state_label(appeal: Appeal, *, now: datetime | None = None) -> str:
    status = AppealStatus(appeal.status)
    if status not in {AppealStatus.OPEN, AppealStatus.IN_REVIEW}:
        return "Закрыта"

    if appeal.sla_deadline_at is None:
        return "Без SLA"

    current_time = now or datetime.now(UTC)
    if appeal.sla_deadline_at <= current_time:
        escalation_level = int(appeal.escalation_level or 0)
        if appeal.escalated_at is not None or escalation_level > 0:
            return f"Просрочена, эскалация L{max(escalation_level, 1)}"
        return "Просрочена"

    return f"До SLA: {_format_duration_compact(appeal.sla_deadline_at - current_time)}"


def _appeal_escalation_marker(appeal: Appeal) -> str:
    escalation_level = int(appeal.escalation_level or 0)
    if appeal.escalated_at is None and escalation_level <= 0:
        return "-"

    normalized_level = max(escalation_level, 1)
    if appeal.escalated_at is None:
        return f"L{normalized_level}"
    return f"L{normalized_level} ({_fmt_ts(appeal.escalated_at)})"


def _violator_status_label(status: str) -> str:
    normalized = status.strip().lower()
    if normalized == "active":
        return "Активный"
    if normalized == "inactive":
        return "Неактивный"
    if normalized == "all":
        return "Все"
    return status


def _parse_ymd_filter(raw: str, *, field_name: str) -> datetime | None:
    value = raw.strip()
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name} date format") from exc
    return parsed.replace(tzinfo=UTC)


def _parse_signed_int(raw: str | None) -> int | None:
    if raw is None:
        return None

    value = raw.strip()
    if not value:
        return None

    try:
        return int(value)
    except ValueError:
        return None


def _parse_non_negative_int(raw: str | None) -> int | None:
    if raw is None or not raw.isdigit():
        return None
    return int(raw)


def _normalize_points_filter_query(raw: str | None) -> PointsEventType | None:
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in {"", "all", "all_types"}:
        return None
    if value in {"feedback", "feedback_approved"}:
        return PointsEventType.FEEDBACK_APPROVED
    if value in {"manual", "manual_adjustment"}:
        return PointsEventType.MANUAL_ADJUSTMENT
    if value in {"boost", "feedback_priority_boost", "priority"}:
        return PointsEventType.FEEDBACK_PRIORITY_BOOST
    if value in {"gboost", "guarantor_priority_boost", "guarant_boost"}:
        return PointsEventType.GUARANTOR_PRIORITY_BOOST
    if value in {"aboost", "appeal_priority_boost", "appeal_boost"}:
        return PointsEventType.APPEAL_PRIORITY_BOOST
    raise HTTPException(status_code=400, detail="Invalid points filter")


def _points_filter_query_value(filter_value: PointsEventType | None) -> str:
    if filter_value is None:
        return "all"
    if filter_value == PointsEventType.FEEDBACK_APPROVED:
        return "feedback"
    if filter_value == PointsEventType.FEEDBACK_PRIORITY_BOOST:
        return "boost"
    if filter_value == PointsEventType.GUARANTOR_PRIORITY_BOOST:
        return "gboost"
    if filter_value == PointsEventType.APPEAL_PRIORITY_BOOST:
        return "aboost"
    return "manual"


def _points_event_label(event_type: PointsEventType) -> str:
    if event_type == PointsEventType.FEEDBACK_APPROVED:
        return "Награда за фидбек"
    if event_type == PointsEventType.FEEDBACK_PRIORITY_BOOST:
        return "Списание за приоритет фидбека"
    if event_type == PointsEventType.GUARANTOR_PRIORITY_BOOST:
        return "Списание за приоритет гаранта"
    if event_type == PointsEventType.APPEAL_PRIORITY_BOOST:
        return "Списание за приоритет апелляции"
    return "Ручная корректировка"
