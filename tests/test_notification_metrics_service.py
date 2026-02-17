from __future__ import annotations

import logging

import pytest

from app.services import notification_metrics_service
from app.services.notification_policy_service import NotificationEventType


class _RedisIncrStub:
    def __init__(self, *, total: int = 1, fail: bool = False) -> None:
        self.total = total
        self.fail = fail
        self.calls: list[tuple[str, int]] = []

    async def incrby(self, key: str, count: int) -> int:
        self.calls.append((key, count))
        if self.fail:
            raise RuntimeError("boom")
        return self.total


class _RedisSnapshotStub:
    def __init__(self, entries: dict[str, str] | None = None, *, fail_scan: bool = False) -> None:
        self.entries = entries or {}
        self.fail_scan = fail_scan
        self.scan_calls: list[tuple[int | str, str, int]] = []
        self.mget_calls: list[tuple[str, ...]] = []

    async def scan(self, *, cursor: int | str, match: str, count: int) -> tuple[int, list[str]]:
        self.scan_calls.append((cursor, match, count))
        if self.fail_scan:
            raise RuntimeError("scan boom")
        if int(cursor) != 0:
            return 0, []
        return 0, list(self.entries.keys())

    async def mget(self, keys: list[str]) -> list[str | None]:
        self.mget_calls.append(tuple(keys))
        return [self.entries.get(key) for key in keys]


@pytest.mark.asyncio
async def test_record_notification_sent_increments_counter_with_normalized_reason(monkeypatch) -> None:
    redis_stub = _RedisIncrStub(total=7)
    monkeypatch.setattr(notification_metrics_service, "redis_client", redis_stub)

    total = await notification_metrics_service.record_notification_sent(
        event_type=NotificationEventType.AUCTION_OUTBID,
        reason="Delivered OK",
    )

    assert total == 7
    assert redis_stub.calls == [
        ("notif:metrics:sent:auction_outbid:delivered_ok", 1),
    ]


@pytest.mark.asyncio
async def test_record_notification_aggregated_uses_count(monkeypatch) -> None:
    redis_stub = _RedisIncrStub(total=9)
    monkeypatch.setattr(notification_metrics_service, "redis_client", redis_stub)

    total = await notification_metrics_service.record_notification_aggregated(
        event_type=NotificationEventType.AUCTION_OUTBID,
        reason="debounce_gate",
        count=3,
    )

    assert total == 9
    assert redis_stub.calls == [
        ("notif:metrics:aggregated:auction_outbid:debounce_gate", 3),
    ]


@pytest.mark.asyncio
async def test_record_notification_metric_logs_warning_on_redis_failure(
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis_stub = _RedisIncrStub(fail=True)
    monkeypatch.setattr(notification_metrics_service, "redis_client", redis_stub)
    caplog.set_level(logging.WARNING)

    total = await notification_metrics_service.record_notification_suppressed(
        event_type=NotificationEventType.SUPPORT,
        reason="policy blocked",
    )

    assert total is None
    assert "notification_metric_failed" in caplog.text
    assert "reason=policy_blocked" in caplog.text


@pytest.mark.asyncio
async def test_load_notification_metrics_snapshot_returns_totals_and_top_suppressed(monkeypatch) -> None:
    redis_stub = _RedisSnapshotStub(
        {
            "notif:metrics:sent:auction_outbid:delivered": "4",
            "notif:metrics:suppressed:auction_outbid:blocked_master": "8",
            "notif:metrics:suppressed:support:forbidden": "2",
            "notif:metrics:aggregated:auction_outbid:debounce_gate": "5",
            "notif:metrics:suppressed:auction_outbid:blocked_event_toggle": "6",
            "notif:metrics:suppressed:support:bad_value": "oops",
            "notif:metrics:unknown:support:bad": "9",
        }
    )
    monkeypatch.setattr(notification_metrics_service, "redis_client", redis_stub)

    snapshot = await notification_metrics_service.load_notification_metrics_snapshot(top_limit=2)

    assert snapshot.sent_total == 4
    assert snapshot.suppressed_total == 16
    assert snapshot.aggregated_total == 5
    assert snapshot.top_suppressed == (
        notification_metrics_service.NotificationMetricBucket(
            event_type=NotificationEventType.AUCTION_OUTBID,
            reason="blocked_master",
            total=8,
        ),
        notification_metrics_service.NotificationMetricBucket(
            event_type=NotificationEventType.AUCTION_OUTBID,
            reason="blocked_event_toggle",
            total=6,
        ),
    )


@pytest.mark.asyncio
async def test_load_notification_metrics_snapshot_returns_empty_snapshot_on_redis_failure(
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis_stub = _RedisSnapshotStub(fail_scan=True)
    monkeypatch.setattr(notification_metrics_service, "redis_client", redis_stub)
    caplog.set_level(logging.WARNING)

    snapshot = await notification_metrics_service.load_notification_metrics_snapshot()

    assert snapshot.sent_total == 0
    assert snapshot.suppressed_total == 0
    assert snapshot.aggregated_total == 0
    assert snapshot.top_suppressed == ()
    assert "notification_metrics_snapshot_failed" in caplog.text
