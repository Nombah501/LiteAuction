from __future__ import annotations

from uuid import UUID

import pytest

from app.services import anti_fool_service


class _RedisSetStub:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[str, str, int, bool]] = []

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> object:
        self.calls.append((key, value, ex, nx))
        return self.result


@pytest.mark.asyncio
async def test_outbid_notification_debounce_disabled_bypasses_redis(monkeypatch) -> None:
    stub = _RedisSetStub(result=True)
    monkeypatch.setattr(anti_fool_service, "redis_client", stub)
    monkeypatch.setattr(anti_fool_service.settings, "outbid_notification_debounce_seconds", 0)

    allowed = await anti_fool_service.acquire_outbid_notification_debounce(
        UUID("12345678-1234-5678-1234-567812345678"),
        42,
    )

    assert allowed is True
    assert stub.calls == []


@pytest.mark.asyncio
async def test_outbid_notification_debounce_uses_user_and_auction_key(monkeypatch) -> None:
    stub = _RedisSetStub(result=True)
    monkeypatch.setattr(anti_fool_service, "redis_client", stub)
    monkeypatch.setattr(anti_fool_service.settings, "outbid_notification_debounce_seconds", 75)

    allowed = await anti_fool_service.acquire_outbid_notification_debounce(
        UUID("12345678-1234-5678-1234-567812345678"),
        55,
    )

    assert allowed is True
    assert stub.calls == [
        ("notif:outbid:debounce:12345678-1234-5678-1234-567812345678:55", "1", 75, True)
    ]


@pytest.mark.asyncio
async def test_outbid_notification_debounce_denies_when_key_exists(monkeypatch) -> None:
    stub = _RedisSetStub(result=None)
    monkeypatch.setattr(anti_fool_service, "redis_client", stub)
    monkeypatch.setattr(anti_fool_service.settings, "outbid_notification_debounce_seconds", 60)

    allowed = await anti_fool_service.acquire_outbid_notification_debounce(
        UUID("12345678-1234-5678-1234-567812345678"),
        99,
    )

    assert allowed is False
