from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.db.enums import AuctionStatus
from app.services import auction_service


@pytest.mark.asyncio
async def test_process_bid_action_non_active_requests_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_auction_by_id(_session, _auction_id, *, for_update: bool = False):
        _ = for_update
        return SimpleNamespace(status=AuctionStatus.ENDED)

    monkeypatch.setattr(auction_service, "get_auction_by_id", fake_get_auction_by_id)

    result = await auction_service.process_bid_action(
        session=object(),
        auction_id=uuid.uuid4(),
        bidder_user_id=1,
        multiplier=1,
        is_buyout=False,
    )

    assert result.success is False
    assert result.should_refresh is True
    assert result.alert_text == "Аукцион не активен"


@pytest.mark.asyncio
async def test_safe_refresh_posts_swallows_refresh_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[uuid.UUID] = []

    async def fake_refresh(_bot, auction_id: uuid.UUID) -> None:
        calls.append(auction_id)
        raise RuntimeError("refresh failed")

    monkeypatch.setattr(auction_service, "refresh_auction_posts", fake_refresh)

    auction_id = uuid.uuid4()
    await auction_service._safe_refresh_auction_posts(bot=object(), auction_id=auction_id)

    assert calls == [auction_id]
