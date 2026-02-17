from __future__ import annotations

from uuid import UUID

import pytest

from app.services import notification_digest_service


class _RedisDigestStub:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._locks: set[str] = set()
        self.expire_calls: list[tuple[str, int]] = []

    async def incr(self, key: str) -> int:
        current = self._counts.get(key, 0) + 1
        self._counts[key] = current
        return current

    async def expire(self, key: str, seconds: int) -> bool:
        self.expire_calls.append((key, seconds))
        return True

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool | None:  # noqa: ARG002
        if nx and key in self._locks:
            return None
        self._locks.add(key)
        return True


@pytest.mark.asyncio
async def test_register_outbid_suppression_first_event_does_not_emit(monkeypatch) -> None:
    redis_stub = _RedisDigestStub()
    monkeypatch.setattr(notification_digest_service, "redis_client", redis_stub)
    monkeypatch.setattr(notification_digest_service.settings, "outbid_notification_digest_window_seconds", 180)

    decision = await notification_digest_service.register_outbid_notification_suppression(
        tg_user_id=42,
        auction_id=UUID("12345678-1234-5678-1234-567812345678"),
    )

    assert decision.suppressed_count == 1
    assert decision.window_seconds == 180
    assert decision.should_emit_digest is False
    assert redis_stub.expire_calls == [
        ("notif:digest:outbid:42:12345678-1234-5678-1234-567812345678:count", 180)
    ]


@pytest.mark.asyncio
async def test_register_outbid_suppression_second_event_emits_digest_once(monkeypatch) -> None:
    redis_stub = _RedisDigestStub()
    monkeypatch.setattr(notification_digest_service, "redis_client", redis_stub)
    monkeypatch.setattr(notification_digest_service.settings, "outbid_notification_digest_window_seconds", 120)

    first = await notification_digest_service.register_outbid_notification_suppression(
        tg_user_id=7,
        auction_id=UUID("12345678-1234-5678-1234-567812345678"),
    )
    second = await notification_digest_service.register_outbid_notification_suppression(
        tg_user_id=7,
        auction_id=UUID("12345678-1234-5678-1234-567812345678"),
    )
    third = await notification_digest_service.register_outbid_notification_suppression(
        tg_user_id=7,
        auction_id=UUID("12345678-1234-5678-1234-567812345678"),
    )

    assert first.should_emit_digest is False
    assert second.should_emit_digest is True
    assert third.should_emit_digest is False
