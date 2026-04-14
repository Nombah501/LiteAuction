from __future__ import annotations

import uuid

import pytest

from app.services.notification_copy_service import (
    seller_completion_text,
    winner_completion_text,
    seller_no_bids_text,
    deal_topic_intro_text,
    moderation_completion_text,
    format_user_mention,
)


@pytest.fixture
def auction_id() -> uuid.UUID:
    return uuid.UUID("a1b2c3d4-5678-9012-abcd-ef1234567890")


def test_seller_completion_text_basic(auction_id: uuid.UUID) -> None:
    text = seller_completion_text(
        auction_id=auction_id,
        description="iPhone 15 Pro Max",
        final_price=15000,
        counterparty_mention="@winner",
        counterparty_role="победитель",
    )
    assert "#a1b2c3d4" in text
    assert "15 000" in text
    assert "@winner" in text


def test_seller_completion_text_buyout(auction_id: uuid.UUID) -> None:
    text = seller_completion_text(
        auction_id=auction_id,
        description="iPhone",
        final_price=20000,
        counterparty_mention="@w",
        counterparty_role="победитель",
        is_buyout=True,
    )
    assert "выкупом" in text


def test_winner_completion_text_basic(auction_id: uuid.UUID) -> None:
    text = winner_completion_text(
        auction_id=auction_id,
        description="iPhone 15 Pro Max",
        final_price=15000,
        counterparty_mention="@seller",
        counterparty_role="продавец",
    )
    assert "#a1b2c3d4" in text
    assert "15 000" in text
    assert "@seller" in text
    assert "выиграл" in text.lower() or "выиграли" in text.lower()


def test_seller_no_bids_text_basic(auction_id: uuid.UUID) -> None:
    text = seller_no_bids_text(
        auction_id=auction_id,
        description="iPhone 15 Pro Max",
        start_price=10000,
    )
    assert "#a1b2c3d4" in text
    assert "10 000" in text
    assert "0" in text


def test_deal_topic_intro_text_basic(auction_id: uuid.UUID) -> None:
    text = deal_topic_intro_text(
        auction_id=auction_id,
        description="iPhone 15 Pro Max",
        final_price=15000,
        seller_mention="@seller",
        winner_mention="@winner",
    )
    assert "#a1b2c3d4" in text
    assert "@seller" in text
    assert "@winner" in text


def test_moderation_completion_text_basic(auction_id: uuid.UUID) -> None:
    text = moderation_completion_text(
        auction_id=auction_id,
        description="iPhone 15 Pro Max",
        final_price=15000,
        bid_count=7,
        seller_mention="@seller (tg: 123)",
        winner_mention="@winner (tg: 456)",
        seller_reputation=42,
        winner_reputation=15,
        has_deal_topic=True,
        has_guarantor=False,
        reason="по таймеру",
    )
    assert "#a1b2c3d4" in text
    assert "15 000" in text
    assert "7" in text
    assert "создан" in text.lower()
    assert "не запрошен" in text.lower()


def test_format_user_mention_with_username() -> None:
    assert format_user_mention(username="john", first_name="John", tg_user_id=123) == "@john"


def test_format_user_mention_without_username() -> None:
    result = format_user_mention(username=None, first_name="John", tg_user_id=123)
    assert "tg://user?id=123" in result
    assert "John" in result
