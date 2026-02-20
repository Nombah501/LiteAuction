from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.admin_queue_preset_telemetry_service import (
    load_workflow_preset_telemetry_segments,
    record_workflow_preset_telemetry_event,
)
from app.web.auth import AdminAuthContext


def _auth() -> AdminAuthContext:
    return AdminAuthContext(
        authorized=True,
        via="telegram",
        role="owner",
        can_manage=True,
        scopes=frozenset({"user:ban"}),
        tg_user_id=900001,
    )


class _RecordingSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, value: object) -> None:
        self.added.append(value)


@pytest.mark.asyncio
async def test_record_workflow_preset_telemetry_normalizes_values() -> None:
    session = _RecordingSession()

    await record_workflow_preset_telemetry_event(
        session,  # type: ignore[arg-type]
        auth=_auth(),
        queue_context="moderation",
        action="select",
        preset_id="42",
        time_to_action_ms="1250",
        reopen_signal="true",
        filter_churn_count="7",
    )

    assert len(session.added) == 1
    event = session.added[0]
    assert getattr(event, "queue_context") == "moderation"
    assert getattr(event, "queue_key") == "complaints"
    assert getattr(event, "action") == "select"
    assert getattr(event, "preset_id") == 42
    assert getattr(event, "time_to_action_ms") == 1250
    assert getattr(event, "reopen_signal") is True
    assert getattr(event, "filter_churn_count") == 7


class _SegmentsResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return list(self._rows)


class _SegmentsSession:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    async def execute(self, _stmt):
        return _SegmentsResult(self._rows)


@pytest.mark.asyncio
async def test_load_workflow_preset_telemetry_segments_computes_rates() -> None:
    rows = [
        SimpleNamespace(
            queue_context="moderation",
            queue_key="complaints",
            preset_id=11,
            events_total=10,
            avg_time_to_action_ms=820.0,
            avg_filter_churn_count=2.5,
            reopen_total=4,
        ),
        SimpleNamespace(
            queue_context="appeals",
            queue_key="appeals",
            preset_id=None,
            events_total=3,
            avg_time_to_action_ms=None,
            avg_filter_churn_count=0.0,
            reopen_total=0,
        ),
    ]

    segments = await load_workflow_preset_telemetry_segments(
        _SegmentsSession(rows),  # type: ignore[arg-type]
        lookback_hours=24,
    )

    assert len(segments) == 2
    assert segments[0]["queue_key"] == "complaints"
    assert segments[0]["preset_id"] == 11
    assert segments[0]["events_total"] == 10
    assert segments[0]["avg_time_to_action_ms"] == 820.0
    assert segments[0]["reopen_rate"] == 0.4
    assert segments[1]["preset_id"] is None
    assert segments[1]["avg_time_to_action_ms"] is None


@pytest.mark.asyncio
async def test_load_workflow_preset_telemetry_segments_rejects_invalid_context() -> None:
    with pytest.raises(ValueError):
        await load_workflow_preset_telemetry_segments(
            _SegmentsSession([]),  # type: ignore[arg-type]
            queue_context="unknown",
            lookback_hours=24,
        )
