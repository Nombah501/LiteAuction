from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from aiogram.types import CallbackQuery

from app.bot.handlers.start import (
    _parse_my_auctions_item_payload,
    _parse_my_auctions_list_payload,
    _render_bid_logs_text,
    _render_my_auctions_list_text,
    _resolve_post_link,
    callback_dashboard_balance,
    callback_dashboard_settings,
)
from app.db.enums import AuctionStatus
from app.services.seller_dashboard_service import SellerAuctionListItem, SellerBidLogItem


class _DummyCallback:
    def __init__(self) -> None:
        self.answers: list[tuple[str, bool]] = []

    async def answer(self, text: str = "", show_alert: bool = False, **_kwargs) -> None:
        self.answers.append((text, show_alert))


def test_parse_my_auctions_list_payload() -> None:
    assert _parse_my_auctions_list_payload("dash:my:list:a:0") == ("a", 0)
    assert _parse_my_auctions_list_payload("dash:my:list:f:3") == ("f", 3)
    assert _parse_my_auctions_list_payload("dash:my:list:x:1") is None
    assert _parse_my_auctions_list_payload("dash:my:list:a:-1") is None


def test_parse_my_auctions_item_payload() -> None:
    payload = _parse_my_auctions_item_payload(
        "dash:my:view:12345678-1234-5678-1234-567812345678:a:0",
        action="view",
    )
    assert payload == (UUID("12345678-1234-5678-1234-567812345678"), "a", 0)
    assert (
        _parse_my_auctions_item_payload(
            "dash:my:view:not-uuid:a:0",
            action="view",
        )
        is None
    )


def test_render_my_auctions_list_text_empty_state() -> None:
    text = _render_my_auctions_list_text(items=[], filter_key="a", page=0, total_items=0)

    assert "Мои аукционы" in text
    assert "пока нет лотов" in text


def test_render_my_auctions_list_text_with_items() -> None:
    now = datetime.now(UTC)
    items = [
        SellerAuctionListItem(
            auction_id=UUID("12345678-1234-5678-1234-567812345678"),
            status=AuctionStatus.ACTIVE,
            start_price=50,
            current_price=95,
            bid_count=5,
            ends_at=now + timedelta(hours=1),
            created_at=now,
        )
    ]

    text = _render_my_auctions_list_text(items=items, filter_key="a", page=0, total_items=1)

    assert "#12345678" in text
    assert "Активен" in text
    assert "$95" in text
    assert "ставок: 5" in text


def test_render_bid_logs_text_contains_actor_and_amount() -> None:
    rows = [
        SellerBidLogItem(
            bid_id=UUID("12345678-1234-5678-1234-567812345678"),
            amount=100,
            created_at=datetime.now(UTC),
            tg_user_id=42,
            username="anna",
            is_removed=False,
        )
    ]

    text = _render_bid_logs_text(
        auction_id=UUID("87654321-4321-8765-4321-876543218765"),
        rows=rows,
    )

    assert "#87654321" in text
    assert "$100" in text
    assert "@anna" in text


def test_resolve_post_link_variants() -> None:
    assert _resolve_post_link(-1001234567890, 17, None) == "https://t.me/c/1234567890/17"
    assert _resolve_post_link(-1001234567890, 17, "publicchat") == "https://t.me/publicchat/17"
    assert _resolve_post_link(-424242, 17, None) is None


@pytest.mark.asyncio
async def test_dashboard_settings_callback_returns_in_development_alert() -> None:
    callback = _DummyCallback()

    await callback_dashboard_settings(cast(CallbackQuery, callback))

    assert callback.answers == [("Раздел «Настройки» в разработке.", True)]


@pytest.mark.asyncio
async def test_dashboard_balance_callback_returns_in_development_alert() -> None:
    callback = _DummyCallback()

    await callback_dashboard_balance(cast(CallbackQuery, callback))

    assert callback.answers == [("Раздел «Баланс» в разработке.", True)]
