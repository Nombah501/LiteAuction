from datetime import UTC, datetime, timedelta

from app.services.queue_sla_health_service import (
    SLA_THRESHOLDS_BY_CONTEXT,
    decide_queue_sla_health,
)
from app.web.main import (
    _parse_complaint_aging_bucket_filter,
    _parse_complaint_sla_health_filter,
    _parse_signal_aging_bucket_filter,
    _parse_signal_sla_health_filter,
)


def test_parse_complaint_sla_health_filter_valid_values():
    for value in ("all", "healthy", "warning", "critical", "overdue", "no_sla"):
        assert _parse_complaint_sla_health_filter(value) == value


def test_parse_complaint_sla_health_filter_invalid():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _parse_complaint_sla_health_filter("invalid")


def test_parse_complaint_aging_bucket_filter_valid_values():
    for value in ("all", "fresh", "aging", "stale", "critical", "overdue", "unknown"):
        assert _parse_complaint_aging_bucket_filter(value) == value


def test_parse_complaint_aging_bucket_filter_invalid():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _parse_complaint_aging_bucket_filter("bad_value")


def test_parse_signal_sla_health_filter_valid_values():
    for value in ("all", "healthy", "warning", "critical", "overdue", "no_sla"):
        assert _parse_signal_sla_health_filter(value) == value


def test_parse_signal_aging_bucket_filter_valid_values():
    for value in ("all", "fresh", "aging", "stale", "critical", "overdue", "unknown"):
        assert _parse_signal_aging_bucket_filter(value) == value


def test_complaint_sla_healthy():
    now = datetime(2026, 4, 11, 12, 0, tzinfo=UTC)
    created = now - timedelta(minutes=30)
    thresholds = SLA_THRESHOLDS_BY_CONTEXT["moderation"]
    deadline = created + thresholds.warning_window + timedelta(hours=1)
    result = decide_queue_sla_health(
        queue_context="moderation", status="OPEN", created_at=created, deadline_at=deadline, now=now,
    )
    assert result.health_state == "healthy"
    assert result.aging_bucket == "fresh"
    assert result.queue_context == "moderation"


def test_complaint_sla_warning():
    now = datetime(2026, 4, 11, 12, 0, tzinfo=UTC)
    thresholds = SLA_THRESHOLDS_BY_CONTEXT["moderation"]
    created = now - timedelta(hours=3)
    deadline = now + thresholds.critical_window + timedelta(minutes=10)
    result = decide_queue_sla_health(
        queue_context="moderation", status="OPEN", created_at=created, deadline_at=deadline, now=now,
    )
    assert result.health_state == "warning"
    assert result.aging_bucket == "aging"


def test_complaint_sla_critical():
    now = datetime(2026, 4, 11, 12, 0, tzinfo=UTC)
    created = now - timedelta(hours=5)
    deadline = now + timedelta(minutes=15)
    result = decide_queue_sla_health(
        queue_context="moderation", status="OPEN", created_at=created, deadline_at=deadline, now=now,
    )
    assert result.health_state == "critical"
    assert result.aging_bucket == "aging"


def test_complaint_sla_overdue():
    now = datetime(2026, 4, 11, 12, 0, tzinfo=UTC)
    created = now - timedelta(hours=10)
    deadline = now - timedelta(hours=1)
    result = decide_queue_sla_health(
        queue_context="moderation", status="OPEN", created_at=created, deadline_at=deadline, now=now,
    )
    assert result.health_state == "overdue"
    assert result.aging_bucket == "overdue"
    assert result.countdown_seconds == 0


def test_complaint_sla_closed():
    now = datetime(2026, 4, 11, 12, 0, tzinfo=UTC)
    created = now - timedelta(hours=10)
    result = decide_queue_sla_health(
        queue_context="moderation", status="RESOLVED", created_at=created, deadline_at=None, now=now,
    )
    assert result.health_state == "closed"
    assert result.aging_bucket == "closed"


def test_complaint_sla_no_deadline():
    now = datetime(2026, 4, 11, 12, 0, tzinfo=UTC)
    created = now - timedelta(hours=2)
    result = decide_queue_sla_health(
        queue_context="moderation", status="OPEN", created_at=created, deadline_at=None, now=now,
    )
    assert result.health_state == "no_sla"
    assert result.aging_bucket == "fresh"


def test_signal_sla_healthy():
    now = datetime(2026, 4, 11, 12, 0, tzinfo=UTC)
    created = now - timedelta(minutes=15)
    thresholds = SLA_THRESHOLDS_BY_CONTEXT["risk"]
    deadline = created + thresholds.warning_window + timedelta(hours=1)
    result = decide_queue_sla_health(
        queue_context="risk", status="OPEN", created_at=created, deadline_at=deadline, now=now,
    )
    assert result.health_state == "healthy"
    assert result.aging_bucket == "fresh"
    assert result.queue_context == "risk"


def test_signal_aging_bucket_stale():
    now = datetime(2026, 4, 11, 12, 0, tzinfo=UTC)
    thresholds = SLA_THRESHOLDS_BY_CONTEXT["risk"]
    created = now - thresholds.aging_aging_max - timedelta(minutes=1)
    result = decide_queue_sla_health(
        queue_context="risk", status="OPEN", created_at=created, deadline_at=None, now=now,
    )
    assert result.aging_bucket == "stale"


def test_signal_aging_bucket_critical():
    now = datetime(2026, 4, 11, 12, 0, tzinfo=UTC)
    thresholds = SLA_THRESHOLDS_BY_CONTEXT["risk"]
    created = now - thresholds.aging_stale_max - timedelta(hours=1)
    result = decide_queue_sla_health(
        queue_context="risk", status="OPEN", created_at=created, deadline_at=None, now=now,
    )
    assert result.aging_bucket == "critical"


def test_unknown_queue_context_fallback():
    now = datetime(2026, 4, 11, 12, 0, tzinfo=UTC)
    created = now - timedelta(hours=1)
    result = decide_queue_sla_health(
        queue_context="nonexistent", status="OPEN", created_at=created, deadline_at=None, now=now,
    )
    assert result.queue_context == "moderation"
    assert result.fallback_applied is True
    assert "unknown_queue_context" in result.fallback_notes
