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
