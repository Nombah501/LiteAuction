from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fnmatch
import logging

import pytest

from app.services import notification_metrics_service
from app.services.notification_policy_service import NotificationEventType


class _RedisIncrStub:
    def __init__(self, *, total: int = 1, fail: bool = False) -> None:
        self.total = total
        self.fail = fail
        self.calls: list[tuple[str, int]] = []
        self.expire_calls: list[tuple[str, int]] = []

    async def incrby(self, key: str, count: int) -> int:
        self.calls.append((key, count))
        if self.fail:
            raise RuntimeError("boom")
        return self.total

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        self.expire_calls.append((key, ttl_seconds))
        return True


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
        keys = [key for key in self.entries if fnmatch.fnmatch(key, match)]
        return 0, keys

    async def mget(self, keys: list[str]) -> list[str | None]:
        self.mget_calls.append(tuple(keys))
        return [self.entries.get(key) for key in keys]


def _alert_codes(snapshot: notification_metrics_service.NotificationMetricsSnapshot) -> set[notification_metrics_service.NotificationAlertCode]:
    return {hint.code for hint in snapshot.alert_hints}


@pytest.mark.asyncio
async def test_record_notification_sent_increments_counter_with_normalized_reason(monkeypatch) -> None:
    redis_stub = _RedisIncrStub(total=7)
    monkeypatch.setattr(notification_metrics_service, "redis_client", redis_stub)

    total = await notification_metrics_service.record_notification_sent(
        event_type=NotificationEventType.AUCTION_OUTBID,
        reason="Delivered OK",
    )

    assert total == 7
    assert redis_stub.calls[0] == ("notif:metrics:sent:auction_outbid:delivered_ok", 1)
    assert redis_stub.calls[1][0].startswith("notif:metrics:h:")
    assert redis_stub.calls[1][0].endswith(":sent:auction_outbid:delivered_ok")
    assert redis_stub.calls[1][1] == 1
    assert redis_stub.expire_calls == [
        (redis_stub.calls[1][0], notification_metrics_service._METRIC_HOURLY_RETENTION_SECONDS),
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
    assert redis_stub.calls[0] == ("notif:metrics:aggregated:auction_outbid:debounce_gate", 3)
    assert redis_stub.calls[1][0].startswith("notif:metrics:h:")
    assert redis_stub.calls[1][0].endswith(":aggregated:auction_outbid:debounce_gate")
    assert redis_stub.calls[1][1] == 3


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
    fixed_now = datetime(2026, 2, 18, 12, 30, tzinfo=timezone.utc)
    h_now = fixed_now.strftime("%Y%m%d%H")
    h_minus_2 = (fixed_now - timedelta(hours=2)).strftime("%Y%m%d%H")
    h_minus_25 = (fixed_now - timedelta(hours=25)).strftime("%Y%m%d%H")
    h_minus_160 = (fixed_now - timedelta(hours=160)).strftime("%Y%m%d%H")
    h_minus_190 = (fixed_now - timedelta(hours=190)).strftime("%Y%m%d%H")

    redis_stub = _RedisSnapshotStub(
        {
            "notif:metrics:sent:auction_outbid:delivered": "40",
            "notif:metrics:suppressed:auction_outbid:blocked_master": "12",
            "notif:metrics:suppressed:support:forbidden": "8",
            "notif:metrics:aggregated:auction_outbid:debounce_gate": "20",
            "notif:metrics:suppressed:support:bad_value": "oops",
            "notif:metrics:unknown:support:bad": "9",
            f"notif:metrics:h:{h_now}:sent:auction_outbid:delivered": "5",
            f"notif:metrics:h:{h_minus_2}:suppressed:auction_outbid:blocked_master": "2",
            f"notif:metrics:h:{h_minus_25}:aggregated:auction_outbid:debounce_gate": "4",
            f"notif:metrics:h:{h_minus_160}:sent:support:delivered": "3",
            f"notif:metrics:h:{h_minus_190}:suppressed:support:forbidden": "9",
        }
    )
    monkeypatch.setattr(notification_metrics_service, "redis_client", redis_stub)

    snapshot = await notification_metrics_service.load_notification_metrics_snapshot(
        top_limit=2,
        now_utc=fixed_now,
    )

    assert snapshot.all_time.sent_total == 40
    assert snapshot.all_time.suppressed_total == 20
    assert snapshot.all_time.aggregated_total == 20
    assert snapshot.last_24h.sent_total == 5
    assert snapshot.last_24h.suppressed_total == 2
    assert snapshot.last_24h.aggregated_total == 0
    assert snapshot.previous_24h.sent_total == 0
    assert snapshot.previous_24h.suppressed_total == 0
    assert snapshot.previous_24h.aggregated_total == 4
    assert snapshot.delta_24h_vs_previous_24h.sent_delta == 5
    assert snapshot.delta_24h_vs_previous_24h.suppressed_delta == 2
    assert snapshot.delta_24h_vs_previous_24h.aggregated_delta == -4
    assert snapshot.last_7d.sent_total == 8
    assert snapshot.last_7d.suppressed_total == 2
    assert snapshot.last_7d.aggregated_total == 4
    assert snapshot.top_suppressed_24h == (
        notification_metrics_service.NotificationMetricBucket(
            event_type=NotificationEventType.AUCTION_OUTBID,
            reason="blocked_master",
            total=2,
        ),
    )
    assert snapshot.top_suppressed_7d == (
        notification_metrics_service.NotificationMetricBucket(
            event_type=NotificationEventType.AUCTION_OUTBID,
            reason="blocked_master",
            total=2,
        ),
    )
    assert snapshot.top_suppressed == (
        notification_metrics_service.NotificationMetricBucket(
            event_type=NotificationEventType.AUCTION_OUTBID,
            reason="blocked_master",
            total=12,
        ),
        notification_metrics_service.NotificationMetricBucket(
            event_type=NotificationEventType.SUPPORT,
            reason="forbidden",
            total=8,
        ),
    )
    assert _alert_codes(snapshot) == {
        notification_metrics_service.NotificationAlertCode.TOP_SUPPRESSION_SHARE_WARNING,
    }


@pytest.mark.asyncio
async def test_load_notification_metrics_snapshot_returns_zeros_when_no_data(monkeypatch) -> None:
    redis_stub = _RedisSnapshotStub({})
    monkeypatch.setattr(notification_metrics_service, "redis_client", redis_stub)

    snapshot = await notification_metrics_service.load_notification_metrics_snapshot()

    assert snapshot.all_time.sent_total == 0
    assert snapshot.all_time.suppressed_total == 0
    assert snapshot.all_time.aggregated_total == 0
    assert snapshot.last_24h.sent_total == 0
    assert snapshot.last_24h.suppressed_total == 0
    assert snapshot.last_24h.aggregated_total == 0
    assert snapshot.previous_24h.sent_total == 0
    assert snapshot.previous_24h.suppressed_total == 0
    assert snapshot.previous_24h.aggregated_total == 0
    assert snapshot.delta_24h_vs_previous_24h.sent_delta == 0
    assert snapshot.delta_24h_vs_previous_24h.suppressed_delta == 0
    assert snapshot.delta_24h_vs_previous_24h.aggregated_delta == 0
    assert snapshot.last_7d.sent_total == 0
    assert snapshot.last_7d.suppressed_total == 0
    assert snapshot.last_7d.aggregated_total == 0
    assert snapshot.top_suppressed_24h == ()
    assert snapshot.top_suppressed_7d == ()
    assert snapshot.top_suppressed == ()
    assert snapshot.alert_hints == ()


@pytest.mark.asyncio
async def test_load_notification_metrics_snapshot_applies_event_and_reason_filters(monkeypatch) -> None:
    fixed_now = datetime(2026, 2, 18, 12, 30, tzinfo=timezone.utc)
    h_now = fixed_now.strftime("%Y%m%d%H")
    h_minus_30 = (fixed_now - timedelta(hours=30)).strftime("%Y%m%d%H")

    redis_stub = _RedisSnapshotStub(
        {
            "notif:metrics:sent:auction_outbid:delivered": "10",
            "notif:metrics:sent:support:delivered": "7",
            "notif:metrics:suppressed:auction_outbid:blocked_master": "5",
            "notif:metrics:suppressed:support:forbidden": "4",
            "notif:metrics:aggregated:auction_outbid:debounce_gate": "3",
            f"notif:metrics:h:{h_now}:sent:support:delivered": "2",
            f"notif:metrics:h:{h_now}:suppressed:support:forbidden": "1",
            f"notif:metrics:h:{h_now}:aggregated:auction_outbid:debounce_gate": "2",
            f"notif:metrics:h:{h_minus_30}:suppressed:support:forbidden": "8",
        }
    )
    monkeypatch.setattr(notification_metrics_service, "redis_client", redis_stub)

    snapshot = await notification_metrics_service.load_notification_metrics_snapshot(
        now_utc=fixed_now,
        event_type_filter=NotificationEventType.SUPPORT,
        reason_filter="forbid",
    )

    assert snapshot.all_time.sent_total == 0
    assert snapshot.all_time.suppressed_total == 4
    assert snapshot.all_time.aggregated_total == 0
    assert snapshot.last_24h.sent_total == 0
    assert snapshot.last_24h.suppressed_total == 1
    assert snapshot.last_24h.aggregated_total == 0
    assert snapshot.previous_24h.sent_total == 0
    assert snapshot.previous_24h.suppressed_total == 8
    assert snapshot.previous_24h.aggregated_total == 0
    assert snapshot.delta_24h_vs_previous_24h.sent_delta == 0
    assert snapshot.delta_24h_vs_previous_24h.suppressed_delta == -7
    assert snapshot.delta_24h_vs_previous_24h.aggregated_delta == 0
    assert snapshot.last_7d.sent_total == 0
    assert snapshot.last_7d.suppressed_total == 9
    assert snapshot.last_7d.aggregated_total == 0
    assert snapshot.top_suppressed_24h == (
        notification_metrics_service.NotificationMetricBucket(
            event_type=NotificationEventType.SUPPORT,
            reason="forbidden",
            total=1,
        ),
    )
    assert snapshot.top_suppressed_7d == (
        notification_metrics_service.NotificationMetricBucket(
            event_type=NotificationEventType.SUPPORT,
            reason="forbidden",
            total=9,
        ),
    )
    assert snapshot.top_suppressed == (
        notification_metrics_service.NotificationMetricBucket(
            event_type=NotificationEventType.SUPPORT,
            reason="forbidden",
            total=4,
        ),
    )
    assert _alert_codes(snapshot) == {
        notification_metrics_service.NotificationAlertCode.TOP_SUPPRESSION_SHARE_WARNING,
        notification_metrics_service.NotificationAlertCode.FORBIDDEN_BAD_REQUEST_SHARE_HIGH,
    }


@pytest.mark.asyncio
async def test_load_notification_metrics_snapshot_delta_supports_positive_negative_and_zero_values(
    monkeypatch,
) -> None:
    fixed_now = datetime(2026, 2, 18, 12, 30, tzinfo=timezone.utc)
    h_now = fixed_now.strftime("%Y%m%d%H")
    h_prev = (fixed_now - timedelta(hours=25)).strftime("%Y%m%d%H")

    redis_stub = _RedisSnapshotStub(
        {
            f"notif:metrics:h:{h_now}:sent:auction_outbid:delivered": "5",
            f"notif:metrics:h:{h_now}:suppressed:auction_outbid:blocked_master": "3",
            f"notif:metrics:h:{h_now}:aggregated:auction_outbid:debounce_gate": "2",
            f"notif:metrics:h:{h_prev}:sent:auction_outbid:delivered": "2",
            f"notif:metrics:h:{h_prev}:suppressed:auction_outbid:blocked_master": "4",
            f"notif:metrics:h:{h_prev}:aggregated:auction_outbid:debounce_gate": "2",
        }
    )
    monkeypatch.setattr(notification_metrics_service, "redis_client", redis_stub)

    snapshot = await notification_metrics_service.load_notification_metrics_snapshot(now_utc=fixed_now)

    assert snapshot.last_24h.sent_total == 5
    assert snapshot.last_24h.suppressed_total == 3
    assert snapshot.last_24h.aggregated_total == 2
    assert snapshot.previous_24h.sent_total == 2
    assert snapshot.previous_24h.suppressed_total == 4
    assert snapshot.previous_24h.aggregated_total == 2
    assert snapshot.delta_24h_vs_previous_24h.sent_delta == 3
    assert snapshot.delta_24h_vs_previous_24h.suppressed_delta == -1
    assert snapshot.delta_24h_vs_previous_24h.aggregated_delta == 0
    assert snapshot.top_suppressed_24h == (
        notification_metrics_service.NotificationMetricBucket(
            event_type=NotificationEventType.AUCTION_OUTBID,
            reason="blocked_master",
            total=3,
        ),
    )
    assert snapshot.top_suppressed_7d == (
        notification_metrics_service.NotificationMetricBucket(
            event_type=NotificationEventType.AUCTION_OUTBID,
            reason="blocked_master",
            total=7,
        ),
    )
    assert _alert_codes(snapshot) == {
        notification_metrics_service.NotificationAlertCode.TOP_SUPPRESSION_SHARE_WARNING,
    }


@pytest.mark.asyncio
async def test_load_notification_metrics_snapshot_window_top_suppressed_sorting_is_deterministic(
    monkeypatch,
) -> None:
    fixed_now = datetime(2026, 2, 18, 12, 30, tzinfo=timezone.utc)
    h_now = fixed_now.strftime("%Y%m%d%H")

    redis_stub = _RedisSnapshotStub(
        {
            f"notif:metrics:h:{h_now}:suppressed:support:forbidden": "5",
            f"notif:metrics:h:{h_now}:suppressed:auction_outbid:zzz": "5",
            f"notif:metrics:h:{h_now}:suppressed:auction_outbid:aaa": "5",
        }
    )
    monkeypatch.setattr(notification_metrics_service, "redis_client", redis_stub)

    snapshot = await notification_metrics_service.load_notification_metrics_snapshot(now_utc=fixed_now, top_limit=3)

    assert snapshot.top_suppressed_24h == (
        notification_metrics_service.NotificationMetricBucket(
            event_type=NotificationEventType.AUCTION_OUTBID,
            reason="aaa",
            total=5,
        ),
        notification_metrics_service.NotificationMetricBucket(
            event_type=NotificationEventType.AUCTION_OUTBID,
            reason="zzz",
            total=5,
        ),
        notification_metrics_service.NotificationMetricBucket(
            event_type=NotificationEventType.SUPPORT,
            reason="forbidden",
            total=5,
        ),
    )
    assert _alert_codes(snapshot) == {
        notification_metrics_service.NotificationAlertCode.FORBIDDEN_BAD_REQUEST_SHARE_HIGH,
    }


@pytest.mark.asyncio
async def test_load_notification_metrics_snapshot_emits_warning_delta_alert(monkeypatch) -> None:
    fixed_now = datetime(2026, 2, 18, 12, 30, tzinfo=timezone.utc)
    h_now = fixed_now.strftime("%Y%m%d%H")
    h_prev = (fixed_now - timedelta(hours=25)).strftime("%Y%m%d%H")

    redis_stub = _RedisSnapshotStub(
        {
            f"notif:metrics:h:{h_now}:suppressed:auction_outbid:blocked_master": "35",
            f"notif:metrics:h:{h_prev}:suppressed:auction_outbid:blocked_master": "5",
        }
    )
    monkeypatch.setattr(notification_metrics_service, "redis_client", redis_stub)

    snapshot = await notification_metrics_service.load_notification_metrics_snapshot(now_utc=fixed_now)

    assert notification_metrics_service.NotificationAlertCode.SUPPRESSED_DELTA_WARNING in _alert_codes(snapshot)


@pytest.mark.asyncio
async def test_load_notification_metrics_snapshot_emits_high_for_forbidden_bad_request_share(monkeypatch) -> None:
    fixed_now = datetime(2026, 2, 18, 12, 30, tzinfo=timezone.utc)
    h_now = fixed_now.strftime("%Y%m%d%H")
    h_prev = (fixed_now - timedelta(hours=25)).strftime("%Y%m%d%H")

    redis_stub = _RedisSnapshotStub(
        {
            f"notif:metrics:h:{h_now}:suppressed:support:forbidden": "4",
            f"notif:metrics:h:{h_now}:suppressed:support:bad_request": "3",
            f"notif:metrics:h:{h_now}:suppressed:auction_outbid:blocked_master": "13",
            f"notif:metrics:h:{h_prev}:suppressed:support:forbidden": "4",
            f"notif:metrics:h:{h_prev}:suppressed:support:bad_request": "3",
            f"notif:metrics:h:{h_prev}:suppressed:auction_outbid:blocked_master": "13",
        }
    )
    monkeypatch.setattr(notification_metrics_service, "redis_client", redis_stub)

    snapshot = await notification_metrics_service.load_notification_metrics_snapshot(now_utc=fixed_now)

    assert notification_metrics_service.NotificationAlertCode.FORBIDDEN_BAD_REQUEST_SHARE_HIGH in _alert_codes(snapshot)


@pytest.mark.asyncio
async def test_load_notification_metrics_snapshot_emits_critical_alert(monkeypatch) -> None:
    fixed_now = datetime(2026, 2, 18, 12, 30, tzinfo=timezone.utc)
    h_now = fixed_now.strftime("%Y%m%d%H")
    h_prev = (fixed_now - timedelta(hours=25)).strftime("%Y%m%d%H")

    redis_stub = _RedisSnapshotStub(
        {
            f"notif:metrics:h:{h_now}:suppressed:auction_outbid:blocked_master": "190",
            f"notif:metrics:h:{h_prev}:suppressed:auction_outbid:blocked_master": "10",
        }
    )
    monkeypatch.setattr(notification_metrics_service, "redis_client", redis_stub)

    snapshot = await notification_metrics_service.load_notification_metrics_snapshot(now_utc=fixed_now)

    assert notification_metrics_service.NotificationAlertCode.SUPPRESSED_DELTA_CRITICAL in _alert_codes(snapshot)


@pytest.mark.asyncio
async def test_load_notification_metrics_snapshot_no_alerts_when_thresholds_not_hit(monkeypatch) -> None:
    fixed_now = datetime(2026, 2, 18, 12, 30, tzinfo=timezone.utc)
    h_now = fixed_now.strftime("%Y%m%d%H")
    h_prev = (fixed_now - timedelta(hours=25)).strftime("%Y%m%d%H")

    redis_stub = _RedisSnapshotStub(
        {
            f"notif:metrics:h:{h_now}:suppressed:auction_outbid:r1": "3",
            f"notif:metrics:h:{h_now}:suppressed:auction_outbid:r2": "3",
            f"notif:metrics:h:{h_now}:suppressed:support:r3": "2",
            f"notif:metrics:h:{h_now}:suppressed:support:r4": "2",
            f"notif:metrics:h:{h_prev}:suppressed:auction_outbid:r1": "3",
            f"notif:metrics:h:{h_prev}:suppressed:auction_outbid:r2": "3",
            f"notif:metrics:h:{h_prev}:suppressed:support:r3": "2",
            f"notif:metrics:h:{h_prev}:suppressed:support:r4": "2",
        }
    )
    monkeypatch.setattr(notification_metrics_service, "redis_client", redis_stub)

    snapshot = await notification_metrics_service.load_notification_metrics_snapshot(now_utc=fixed_now)

    assert snapshot.alert_hints == ()


@pytest.mark.asyncio
async def test_load_notification_metrics_snapshot_returns_empty_snapshot_on_redis_failure(
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis_stub = _RedisSnapshotStub(fail_scan=True)
    monkeypatch.setattr(notification_metrics_service, "redis_client", redis_stub)
    caplog.set_level(logging.WARNING)

    snapshot = await notification_metrics_service.load_notification_metrics_snapshot()

    assert snapshot.all_time.sent_total == 0
    assert snapshot.all_time.suppressed_total == 0
    assert snapshot.all_time.aggregated_total == 0
    assert snapshot.last_24h.sent_total == 0
    assert snapshot.last_24h.suppressed_total == 0
    assert snapshot.last_24h.aggregated_total == 0
    assert snapshot.previous_24h.sent_total == 0
    assert snapshot.previous_24h.suppressed_total == 0
    assert snapshot.previous_24h.aggregated_total == 0
    assert snapshot.delta_24h_vs_previous_24h.sent_delta == 0
    assert snapshot.delta_24h_vs_previous_24h.suppressed_delta == 0
    assert snapshot.delta_24h_vs_previous_24h.aggregated_delta == 0
    assert snapshot.last_7d.sent_total == 0
    assert snapshot.last_7d.suppressed_total == 0
    assert snapshot.last_7d.aggregated_total == 0
    assert snapshot.top_suppressed_24h == ()
    assert snapshot.top_suppressed_7d == ()
    assert snapshot.top_suppressed == ()
    assert snapshot.alert_hints == ()
    assert "notification_metrics_snapshot_failed" in caplog.text
