from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from aiogram.types import CallbackQuery

from app.bot.handlers.start import (
    _SETTINGS_TOGGLE_EVENTS,
    _extract_report_auction_id,
    _parse_my_auctions_item_payload,
    _parse_my_auctions_list_payload,
    _render_bid_logs_text,
    _render_my_auction_detail_text,
    _render_my_auctions_list_text,
    _resolve_post_link,
    _settings_keyboard,
    callback_notification_mute_type,
    callback_notification_snooze_auction,
    callback_dashboard_balance,
    callback_dashboard_settings,
)
from app.db.enums import AuctionStatus
from app.services.notification_policy_service import (
    AuctionNotificationSnoozeView,
    NotificationEventType,
    NotificationPreset,
    NotificationSettingsSnapshot,
)
from app.services.seller_dashboard_service import SellerAuctionListItem, SellerBidLogItem


class _DummyCallback:
    def __init__(self) -> None:
        self.answers: list[tuple[str, bool]] = []
        self.from_user: object | None = None
        self.message = None
        self.data: str | None = None

    async def answer(self, text: str = "", show_alert: bool = False, **_kwargs) -> None:
        self.answers.append((text, show_alert))


def test_parse_my_auctions_list_payload() -> None:
    assert _parse_my_auctions_list_payload("dash:my:list:a:0") == ("a", "n", 0)
    assert _parse_my_auctions_list_payload("dash:my:list:f:e:3") == ("f", "e", 3)
    assert _parse_my_auctions_list_payload("dash:my:list:f:b:3") == ("f", "b", 3)
    assert _parse_my_auctions_list_payload("dash:my:list:x:1") is None
    assert _parse_my_auctions_list_payload("dash:my:list:a:z:1") is None
    assert _parse_my_auctions_list_payload("dash:my:list:a:-1") is None


def test_parse_my_auctions_item_payload() -> None:
    payload = _parse_my_auctions_item_payload(
        "dash:my:view:12345678-1234-5678-1234-567812345678:a:e:0",
        action="view",
    )
    assert payload == (UUID("12345678-1234-5678-1234-567812345678"), "a", "e", 0)
    legacy_payload = _parse_my_auctions_item_payload(
        "dash:my:view:12345678-1234-5678-1234-567812345678:a:0",
        action="view",
    )
    assert legacy_payload == (UUID("12345678-1234-5678-1234-567812345678"), "a", "n", 0)
    assert (
        _parse_my_auctions_item_payload(
            "dash:my:view:not-uuid:a:0",
            action="view",
        )
        is None
    )


def test_extract_report_auction_id_from_start_payload() -> None:
    assert _extract_report_auction_id("report_12345678-1234-5678-1234-567812345678") == UUID(
        "12345678-1234-5678-1234-567812345678"
    )
    assert _extract_report_auction_id("report_invalid") is None
    assert _extract_report_auction_id("other_123") is None


def test_render_my_auctions_list_text_empty_state() -> None:
    text = _render_my_auctions_list_text(items=[], filter_key="a", sort_key="n", page=0, total_items=0)

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

    text = _render_my_auctions_list_text(items=items, filter_key="a", sort_key="e", page=0, total_items=1)

    assert "#12345678" in text
    assert "Активен" in text
    assert "$95" in text
    assert "ставок: 5" in text
    assert "Скоро финиш" in text


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


def test_render_my_auction_detail_text_contains_outcome_metrics() -> None:
    item = SellerAuctionListItem(
        auction_id=UUID("12345678-1234-5678-1234-567812345678"),
        status=AuctionStatus.ENDED,
        start_price=50,
        current_price=95,
        bid_count=5,
        ends_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )

    text = _render_my_auction_detail_text(item)

    assert "Финальная цена" in text
    assert "Прирост к старту: +$45 (+90.0%)" in text
    assert "Ср. прирост на ставку: $9.00" in text


@pytest.mark.asyncio
async def test_dashboard_settings_callback_ignores_missing_user() -> None:
    callback = _DummyCallback()

    await callback_dashboard_settings(cast(CallbackQuery, callback))

    assert callback.answers == []


@pytest.mark.asyncio
async def test_dashboard_balance_callback_returns_in_development_alert() -> None:
    callback = _DummyCallback()

    await callback_dashboard_balance(cast(CallbackQuery, callback))

    assert callback.answers == [("Раздел «Баланс» в разработке.", True)]


def test_settings_toggle_mapping_contains_all_supported_events() -> None:
    assert _SETTINGS_TOGGLE_EVENTS == {
        "outbid": NotificationEventType.AUCTION_OUTBID,
        "finish": NotificationEventType.AUCTION_FINISH,
        "win": NotificationEventType.AUCTION_WIN,
        "mod": NotificationEventType.AUCTION_MOD_ACTION,
        "points": NotificationEventType.POINTS,
        "support": NotificationEventType.SUPPORT,
    }


def test_settings_keyboard_contains_unsnooze_buttons() -> None:
    snapshot = NotificationSettingsSnapshot(
        master_enabled=True,
        preset=NotificationPreset.RECOMMENDED,
        outbid_enabled=True,
        auction_finish_enabled=True,
        auction_win_enabled=True,
        auction_mod_actions_enabled=True,
        points_enabled=True,
        support_enabled=True,
        configured=True,
    )
    snoozes = [
        AuctionNotificationSnoozeView(
            auction_id=UUID("12345678-1234-5678-1234-567812345678"),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    ]

    keyboard = _settings_keyboard(snapshot, snoozes=snoozes)
    callback_data = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    }
    assert "dash:settings:unsnooze:12345678-1234-5678-1234-567812345678" in callback_data


def test_settings_keyboard_contains_unmute_buttons_for_disabled_events() -> None:
    snapshot = NotificationSettingsSnapshot(
        master_enabled=True,
        preset=NotificationPreset.CUSTOM,
        outbid_enabled=False,
        auction_finish_enabled=True,
        auction_win_enabled=False,
        auction_mod_actions_enabled=True,
        points_enabled=True,
        support_enabled=False,
        configured=True,
    )

    keyboard = _settings_keyboard(snapshot, snoozes=[])
    callback_data = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    }
    assert "dash:settings:unmute:outbid" in callback_data
    assert "dash:settings:unmute:win" in callback_data
    assert "dash:settings:unmute:support" in callback_data


@pytest.mark.asyncio
async def test_notification_snooze_callback_invalid_payload_returns_clear_alert() -> None:
    callback = _DummyCallback()
    callback.from_user = object()
    callback.data = "notif:snooze:not-a-uuid"

    await callback_notification_snooze_auction(cast(CallbackQuery, callback))

    assert callback.answers == [
        ("Кнопка устарела. Откройте /settings и настройте уведомления снова.", True)
    ]


@pytest.mark.asyncio
async def test_notification_mute_callback_invalid_payload_returns_clear_alert() -> None:
    callback = _DummyCallback()
    callback.from_user = object()
    callback.data = "notif:mute:unknown"

    await callback_notification_mute_type(cast(CallbackQuery, callback))

    assert callback.answers == [
        ("Кнопка устарела. Тип уведомления можно изменить в /settings.", True)
    ]
