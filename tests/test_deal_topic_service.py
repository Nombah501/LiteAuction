from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.deal_topic_service import (
    find_active_deal_topic,
    close_deal_topic,
)
from app.db.enums import DealTopicStatus


@pytest.fixture
def auction_id() -> uuid.UUID:
    return uuid.UUID("a1b2c3d4-5678-9012-abcd-ef1234567890")


@pytest.mark.asyncio
async def test_find_active_deal_topic_returns_none_when_not_exists(auction_id: uuid.UUID) -> None:
    mock_session = AsyncMock()
    mock_session.scalar.return_value = None
    result = await find_active_deal_topic(mock_session, auction_id=auction_id)
    assert result is None


@pytest.mark.asyncio
async def test_find_active_deal_topic_returns_existing(auction_id: uuid.UUID) -> None:
    mock_session = AsyncMock()
    existing = MagicMock()
    existing.auction_id = auction_id
    existing.status = DealTopicStatus.ACTIVE
    mock_session.scalar.return_value = existing
    result = await find_active_deal_topic(mock_session, auction_id=auction_id)
    assert result is existing


@pytest.mark.asyncio
async def test_close_deal_topic_updates_status(auction_id: uuid.UUID) -> None:
    mock_session = AsyncMock()
    deal = MagicMock()
    deal.status = DealTopicStatus.ACTIVE
    deal.closed_at = None
    await close_deal_topic(mock_session, deal=deal)
    assert deal.status == DealTopicStatus.CLOSED
    assert deal.closed_at is not None
    mock_session.flush.assert_called_once()
