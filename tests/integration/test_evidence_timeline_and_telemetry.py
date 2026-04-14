from __future__ import annotations

import pytest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from sqlalchemy import select

from app.db.models import (
    AdminQueuePresetTelemetryEvent,
    Complaint,
    FraudSignal,
    ModerationLog,
    User,
)
from app.services.admin_queue_preset_telemetry_service import (
    load_workflow_preset_telemetry_segments,
)
from app.web.routers.complaints import _render_complaint_detail_section
from app.web.routers.signals import _render_signal_detail_section


pytestmark = pytest.mark.asyncio


async def _ensure_user(session, *, tg_user_id: int, username: str = "test_user") -> User:
    user = await session.scalar(select(User).where(User.tg_user_id == tg_user_id))
    if user is not None:
        return user
    user = User(tg_user_id=tg_user_id, username=username)
    session.add(user)
    await session.flush()
    return user


async def test_complaint_evidence_timeline_rendering(db_session):
    reporter = await _ensure_user(db_session, tg_user_id=90001, username="reporter_tl")
    target = await _ensure_user(db_session, tg_user_id=90002, username="target_tl")
    actor = await _ensure_user(db_session, tg_user_id=90003, username="actor_tl")

    complaint = Complaint(
        auction_id=None,
        reporter_user_id=reporter.id,
        target_user_id=target.id,
        reason="Integration test complaint",
        status="OPEN",
    )
    db_session.add(complaint)
    await db_session.flush()

    mod_log = ModerationLog(
        actor_user_id=actor.id,
        target_user_id=target.id,
        auction_id=None,
        action="RESOLVE_COMPLAINT",
        reason="resolved in test",
        payload={
            "rationale_artifact": {
                "summary": "test rationale artifact",
                "actor_user_id": actor.id,
                "actor_tg_user_id": 90003,
                "source": "web",
                "recorded_at": datetime.now(UTC).isoformat(),
                "immutable": True,
            }
        },
    )
    db_session.add(mod_log)
    await db_session.flush()

    request = MagicMock()
    result = await _render_complaint_detail_section(
        db_session,
        row_id=complaint.id,
        section="primary",
        request=request,
    )
    assert result["ok"] is True
    assert "Complaint created" in result["html"]

    result_secondary = await _render_complaint_detail_section(
        db_session,
        row_id=complaint.id,
        section="secondary",
        request=request,
    )
    assert result_secondary["ok"] is True
    assert "test rationale artifact" in result_secondary["html"]

    result_audit = await _render_complaint_detail_section(
        db_session,
        row_id=complaint.id,
        section="audit",
        request=request,
    )
    assert result_audit["ok"] is True
    assert "append_only" in result_audit["html"]


async def test_signal_evidence_timeline_rendering(db_session):
    signal_user = await _ensure_user(db_session, tg_user_id=90011, username="signal_user_tl")

    signal = FraudSignal(
        auction_id=None,
        user_id=signal_user.id,
        score=85,
        status="OPEN",
        reason="integration test signal",
    )
    db_session.add(signal)
    await db_session.flush()

    request = MagicMock()
    result = await _render_signal_detail_section(
        db_session,
        row_id=signal.id,
        section="primary",
        request=request,
    )
    assert result["ok"] is True
    assert "Fraud signal created" in result["html"]
    assert "score=85" in result["html"]


async def test_telemetry_trend_aggregation(db_session):
    now = datetime.now(UTC)
    recent_time = now - timedelta(hours=12)
    old_time = now - timedelta(hours=10 * 24)

    for i in range(6):
        event = AdminQueuePresetTelemetryEvent(
            queue_context="moderation",
            queue_key="complaints",
            preset_id=1,
            action="select",
            actor_subject_key="test_actor",
            time_to_action_ms=500 + i * 100,
            reopen_signal=False,
            filter_churn_count=i,
            created_at=recent_time + timedelta(minutes=i),
        )
        db_session.add(event)

    for i in range(6):
        event = AdminQueuePresetTelemetryEvent(
            queue_context="moderation",
            queue_key="complaints",
            preset_id=1,
            action="select",
            actor_subject_key="test_actor",
            time_to_action_ms=200 + i * 50,
            reopen_signal=False,
            filter_churn_count=i,
            created_at=old_time + timedelta(minutes=i),
        )
        db_session.add(event)

    await db_session.flush()

    segments = await load_workflow_preset_telemetry_segments(
        db_session,
        queue_context="moderation",
        lookback_hours=24 * 7,
    )
    assert len(segments) > 0
    matching = [
        s for s in segments if s.get("preset_id") == 1 and s.get("queue_context") == "moderation"
    ]
    assert len(matching) > 0
    segment = matching[0]
    assert int(segment["events_total"]) == 6
    assert segment["time_to_action_delta_ms"] is not None


async def test_telemetry_low_sample_guardrail(db_session):
    now = datetime.now(UTC)
    recent_time = now - timedelta(hours=12)

    for i in range(2):
        event = AdminQueuePresetTelemetryEvent(
            queue_context="risk",
            queue_key="signals",
            preset_id=None,
            action="save",
            actor_subject_key="guardrail_actor",
            time_to_action_ms=100,
            reopen_signal=False,
            filter_churn_count=0,
            created_at=recent_time + timedelta(minutes=i),
        )
        db_session.add(event)

    await db_session.flush()

    segments = await load_workflow_preset_telemetry_segments(
        db_session,
        queue_context="risk",
        lookback_hours=24 * 7,
    )
    if segments:
        assert segments[0].get("trend_low_sample_guardrail") is True


async def test_rationale_artifact_immutability(db_session):
    actor = await _ensure_user(db_session, tg_user_id=90061, username="immut_actor")

    log = ModerationLog(
        actor_user_id=actor.id,
        target_user_id=None,
        auction_id=None,
        action="FREEZE_AUCTION",
        reason="test immutability",
        payload={
            "rationale_artifact": {
                "summary": "original summary",
                "actor_user_id": actor.id,
                "actor_tg_user_id": 90061,
                "source": "web",
                "recorded_at": datetime.now(UTC).isoformat(),
                "immutable": True,
            }
        },
    )
    db_session.add(log)
    await db_session.flush()

    loaded = await db_session.scalar(select(ModerationLog).where(ModerationLog.id == log.id))
    artifact = loaded.payload["rationale_artifact"]
    assert artifact["immutable"] is True
    assert artifact["summary"] == "original summary"
