from __future__ import annotations

import pytest

from app.services import notification_quiet_hours_service
from app.services.notification_policy_service import NotificationEventType


class _RedisQuietHoursStub:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expire_calls: list[tuple[str, int]] = []

    async def incr(self, key: str) -> int:
        current = self.values.get(key, 0) + 1
        self.values[key] = current
        return current

    async def expire(self, key: str, seconds: int) -> bool:
        self.expire_calls.append((key, seconds))
        return True

    async def getdel(self, key: str) -> str | None:
        value = self.values.pop(key, None)
        if value is None:
            return None
        return str(value)


@pytest.mark.asyncio
async def test_defer_notification_event_increments_and_sets_ttl(monkeypatch) -> None:
    redis_stub = _RedisQuietHoursStub()
    monkeypatch.setattr(notification_quiet_hours_service, "redis_client", redis_stub)

    first = await notification_quiet_hours_service.defer_notification_event(
        tg_user_id=123,
        event_type=NotificationEventType.POINTS,
    )
    second = await notification_quiet_hours_service.defer_notification_event(
        tg_user_id=123,
        event_type=NotificationEventType.POINTS,
    )

    assert first == 1
    assert second == 2
    assert redis_stub.expire_calls


@pytest.mark.asyncio
async def test_pop_deferred_notification_count_returns_and_clears(monkeypatch) -> None:
    redis_stub = _RedisQuietHoursStub()
    monkeypatch.setattr(notification_quiet_hours_service, "redis_client", redis_stub)

    await notification_quiet_hours_service.defer_notification_event(
        tg_user_id=999,
        event_type=NotificationEventType.AUCTION_OUTBID,
    )
    await notification_quiet_hours_service.defer_notification_event(
        tg_user_id=999,
        event_type=NotificationEventType.AUCTION_OUTBID,
    )

    count = await notification_quiet_hours_service.pop_deferred_notification_count(
        tg_user_id=999,
        event_type=NotificationEventType.AUCTION_OUTBID,
    )
    second = await notification_quiet_hours_service.pop_deferred_notification_count(
        tg_user_id=999,
        event_type=NotificationEventType.AUCTION_OUTBID,
    )

    assert count == 2
    assert second == 0
