from __future__ import annotations

import fnmatch
import logging

import pytest

from app.services import bot_funnel_metrics_service


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

    async def scan(self, *, cursor: int | str, match: str, count: int) -> tuple[int, list[str]]:  # noqa: ARG002
        if self.fail_scan:
            raise RuntimeError("scan boom")
        if int(cursor) != 0:
            return 0, []
        keys = [key for key in self.entries if fnmatch.fnmatch(key, match)]
        return 0, keys

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.entries.get(key) for key in keys]


@pytest.mark.asyncio
async def test_record_bot_funnel_event_normalizes_context_and_failure_reason(monkeypatch) -> None:
    redis_stub = _RedisIncrStub(total=7)
    monkeypatch.setattr(bot_funnel_metrics_service, "redis_client", redis_stub)

    total = await bot_funnel_metrics_service.record_bot_funnel_event(
        journey=bot_funnel_metrics_service.BotFunnelJourney.BID,
        step=bot_funnel_metrics_service.BotFunnelStep.FAIL,
        actor_role=bot_funnel_metrics_service.BotFunnelActorRole.BIDDER,
        context_key="callback:Bid x3",
        failure_reason="Duplicate Bid",
    )

    assert total == 7
    assert redis_stub.calls == [("bot:funnel:bid:fail:bidder:callback_bid_x3:duplicate_bid", 1)]


@pytest.mark.asyncio
async def test_record_bot_funnel_event_logs_warning_on_redis_error(
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis_stub = _RedisIncrStub(fail=True)
    monkeypatch.setattr(bot_funnel_metrics_service, "redis_client", redis_stub)
    caplog.set_level(logging.WARNING)

    total = await bot_funnel_metrics_service.record_bot_funnel_event(
        journey=bot_funnel_metrics_service.BotFunnelJourney.COMPLAINT,
        step=bot_funnel_metrics_service.BotFunnelStep.START,
        actor_role=bot_funnel_metrics_service.BotFunnelActorRole.BIDDER,
        context_key="callback:report",
    )

    assert total is None
    assert "bot_funnel_metric_failed" in caplog.text


@pytest.mark.asyncio
async def test_load_bot_funnel_snapshot_returns_conversion_and_dropoffs(monkeypatch) -> None:
    redis_stub = _RedisSnapshotStub(
        {
            "bot:funnel:auction_create:start:seller:command_newauction:ok": "10",
            "bot:funnel:auction_create:complete:seller:wizard_finalize:ok": "7",
            "bot:funnel:auction_create:fail:seller:command_newauction:blacklisted": "2",
            "bot:funnel:auction_create:fail:seller:wizard_finalize:preview_unavailable": "1",
            "bot:funnel:bid:start:bidder:callback_bid:ok": "20",
            "bot:funnel:bid:complete:bidder:callback_bid:ok": "12",
            "bot:funnel:bid:fail:bidder:callback_bid:cooldown": "5",
            "bot:funnel:bid:fail:bidder:callback_bid:action_rejected": "3",
            "bot:funnel:broken": "99",
            "bot:funnel:complaint:fail:bidder:callback_report:service_error": "oops",
        }
    )
    monkeypatch.setattr(bot_funnel_metrics_service, "redis_client", redis_stub)

    snapshot = await bot_funnel_metrics_service.load_bot_funnel_snapshot(top_limit=2)

    assert snapshot.total_starts == 30
    assert snapshot.total_completes == 19
    assert snapshot.total_fails == 11
    assert len(snapshot.journey_summaries) == 2

    auction_summary = snapshot.journey_summaries[0]
    bid_summary = snapshot.journey_summaries[1]

    assert auction_summary.journey == bot_funnel_metrics_service.BotFunnelJourney.AUCTION_CREATE
    assert auction_summary.starts == 10
    assert auction_summary.completes == 7
    assert auction_summary.fails == 3
    assert auction_summary.conversion_rate_percent == 70.0
    assert auction_summary.top_drop_offs[0].reason == "blacklisted"
    assert auction_summary.top_drop_offs[0].total == 2

    assert bid_summary.journey == bot_funnel_metrics_service.BotFunnelJourney.BID
    assert bid_summary.starts == 20
    assert bid_summary.completes == 12
    assert bid_summary.fails == 8
    assert bid_summary.conversion_rate_percent == 60.0
    assert bid_summary.top_drop_offs[0].reason == "cooldown"
    assert bid_summary.top_drop_offs[0].total == 5

    assert snapshot.top_drop_offs[0].journey == bot_funnel_metrics_service.BotFunnelJourney.BID
    assert snapshot.top_drop_offs[0].reason == "cooldown"
    assert snapshot.top_drop_offs[0].total == 5


@pytest.mark.asyncio
async def test_load_bot_funnel_snapshot_returns_empty_on_scan_failure(monkeypatch) -> None:
    redis_stub = _RedisSnapshotStub(fail_scan=True)
    monkeypatch.setattr(bot_funnel_metrics_service, "redis_client", redis_stub)

    snapshot = await bot_funnel_metrics_service.load_bot_funnel_snapshot()

    assert snapshot.journey_summaries == ()
    assert snapshot.top_drop_offs == ()
    assert snapshot.total_starts == 0
    assert snapshot.total_completes == 0
    assert snapshot.total_fails == 0
