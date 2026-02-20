from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.queue_sla_health_service import SLA_THRESHOLDS_BY_CONTEXT, decide_queue_sla_health


def _now() -> datetime:
    return datetime(2026, 2, 20, 12, 0, tzinfo=UTC)


def test_thresholds_are_bounded_for_all_contexts() -> None:
    for thresholds in SLA_THRESHOLDS_BY_CONTEXT.values():
        assert thresholds.warning_window > timedelta(0)
        assert thresholds.critical_window > timedelta(0)
        assert thresholds.warning_window > thresholds.critical_window
        assert thresholds.aging_fresh_max > timedelta(0)
        assert thresholds.aging_fresh_max < thresholds.aging_aging_max < thresholds.aging_stale_max


def test_decide_queue_sla_health_reports_healthy_with_long_remaining() -> None:
    now = _now()
    decision = decide_queue_sla_health(
        queue_context="appeals",
        status="OPEN",
        created_at=now - timedelta(hours=1),
        deadline_at=now + timedelta(hours=10),
        now=now,
    )

    assert decision.queue_context == "appeals"
    assert decision.health_state == "healthy"
    assert decision.aging_bucket == "fresh"
    assert decision.reason_code == "within_budget"
    assert decision.countdown_seconds == 36000
    assert decision.fallback_applied is False


def test_decide_queue_sla_health_reports_warning_window() -> None:
    now = _now()
    decision = decide_queue_sla_health(
        queue_context="moderation",
        status="IN_REVIEW",
        created_at=now - timedelta(hours=5),
        deadline_at=now + timedelta(hours=2),
        now=now,
    )

    assert decision.health_state == "warning"
    assert decision.aging_bucket == "aging"
    assert decision.reason_code == "warning_window"


def test_decide_queue_sla_health_reports_critical_window() -> None:
    now = _now()
    decision = decide_queue_sla_health(
        queue_context="risk",
        status="OPEN",
        created_at=now - timedelta(hours=4),
        deadline_at=now + timedelta(minutes=20),
        now=now,
    )

    assert decision.health_state == "critical"
    assert decision.aging_bucket == "stale"
    assert decision.reason_code == "critical_window"


def test_decide_queue_sla_health_reports_overdue() -> None:
    now = _now()
    decision = decide_queue_sla_health(
        queue_context="feedback",
        status="VISIBLE",
        created_at=now - timedelta(hours=20),
        deadline_at=now - timedelta(minutes=1),
        now=now,
    )

    assert decision.health_state == "overdue"
    assert decision.aging_bucket == "overdue"
    assert decision.reason_code == "deadline_elapsed"
    assert decision.countdown_seconds == 0


def test_decide_queue_sla_health_handles_missing_deadline() -> None:
    now = _now()
    decision = decide_queue_sla_health(
        queue_context="appeals",
        status="OPEN",
        created_at=now - timedelta(hours=16),
        deadline_at=None,
        now=now,
    )

    assert decision.health_state == "no_sla"
    assert decision.aging_bucket == "stale"
    assert decision.reason_code == "missing_deadline"
    assert decision.countdown_seconds is None


def test_decide_queue_sla_health_falls_back_for_unknown_context_and_status() -> None:
    now = _now()
    decision = decide_queue_sla_health(
        queue_context="mystery",
        status="",
        created_at=now - timedelta(hours=3),
        deadline_at=now + timedelta(hours=3),
        now=now,
    )

    assert decision.queue_context == "moderation"
    assert decision.health_state == "warning"
    assert decision.fallback_applied is True
    assert decision.fallback_notes == ("unknown_queue_context", "missing_status_assumed_open")


def test_decide_queue_sla_health_short_circuits_closed_status() -> None:
    now = _now()
    decision = decide_queue_sla_health(
        queue_context="appeals",
        status="RESOLVED",
        created_at=now - timedelta(hours=30),
        deadline_at=now - timedelta(hours=5),
        now=now,
    )

    assert decision.health_state == "closed"
    assert decision.aging_bucket == "closed"
    assert decision.reason_code == "closed_status"
    assert decision.countdown_seconds is None


def test_decide_queue_sla_health_marks_invalid_timing_inputs() -> None:
    now = _now()
    decision = decide_queue_sla_health(
        queue_context="appeals",
        status="OPEN",
        created_at=now + timedelta(hours=1),
        deadline_at=now,
        now=now,
    )

    assert decision.health_state == "overdue"
    assert decision.fallback_applied is True
    assert "created_at_in_future" in decision.fallback_notes
    assert "deadline_before_created_at" in decision.fallback_notes
