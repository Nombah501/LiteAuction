from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from aiogram.types import CallbackQuery

from app.bot.handlers.start import (
    _SETTINGS_TOGGLE_EVENTS,
    _balance_keyboard,
    _extract_report_auction_id,
    _parse_my_auctions_item_payload,
    _parse_my_auctions_list_payload,
    _render_balance_text,
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
from app.db.enums import AuctionStatus, PointsEventType
from app.services.appeal_service import AppealPriorityBoostPolicy
from app.services.feedback_service import FeedbackPriorityBoostPolicy
from app.services.notification_policy_service import (
    AuctionNotificationSnoozeView,
    NotificationEventType,
    NotificationPreset,
    NotificationSettingsSnapshot,
)
from app.services.guarantor_service import GuarantorPriorityBoostPolicy
from app.services.points_service import UserPointsSummary
from app.services.seller_dashboard_service import SellerAuctionListItem, SellerBidLogItem


class _DummyCallback:
    def __init__(self) -> None:
        self.answers: list[tuple[str, bool]] = []
        self.from_user: object | None = None
        self.message: object | None = None
        self.data: str | None = None

    async def answer(self, text: str = "", show_alert: bool = False, **_kwargs) -> None:
        self.answers.append((text, show_alert))


class _DummyEditableMessage:
    def __init__(self) -> None:
        self.edits: list[str] = []
        self.answers: list[str] = []

    async def edit_text(self, text: str, **_kwargs) -> None:
        self.edits.append(text)

    async def answer(self, text: str, **_kwargs) -> None:
        self.answers.append(text)


class _DummyTransaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *_args) -> bool:
        return False


class _DummySession:
    def begin(self) -> _DummyTransaction:
        return _DummyTransaction()


class _DummySessionFactory:
    async def __aenter__(self) -> _DummySession:
        return _DummySession()

    async def __aexit__(self, *_args) -> bool:
        return False
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
async def test_dashboard_balance_callback_shows_error_without_message() -> None:
    callback = _DummyCallback()
    callback.from_user = SimpleNamespace(id=7)

    await callback_dashboard_balance(cast(CallbackQuery, callback))

    assert callback.answers == [("Не удалось открыть раздел «Баланс»", True)]


@pytest.mark.asyncio
async def test_dashboard_balance_callback_renders_balance_card(monkeypatch) -> None:
    callback = _DummyCallback()
    callback.from_user = SimpleNamespace(id=777, username="alice")
    message = _DummyEditableMessage()
    callback.message = message

    monkeypatch.setattr("app.bot.handlers.start.SessionFactory", lambda: _DummySessionFactory())

    async def _fake_upsert_user(*_args, **_kwargs):
        return SimpleNamespace(id=501)

    async def _fake_enforce_callback_topic(*_args, **_kwargs) -> bool:
        return True

    async def _fake_summary(*_args, **_kwargs) -> UserPointsSummary:
        return UserPointsSummary(balance=120, total_earned=240, total_spent=120, operations_count=4)

    async def _fake_entries(*_args, **_kwargs):
        return [
            SimpleNamespace(
                created_at=datetime.now(UTC),
                amount=-20,
                event_type=PointsEventType.FEEDBACK_PRIORITY_BOOST.value,
            )
        ]

    async def _fake_feedback_policy(*_args, **_kwargs) -> FeedbackPriorityBoostPolicy:
        return FeedbackPriorityBoostPolicy(
            enabled=True,
            cost_points=20,
            daily_limit=3,
            used_today=1,
            remaining_today=2,
            cooldown_seconds=300,
            cooldown_remaining_seconds=0,
        )

    async def _fake_guarantor_policy(*_args, **_kwargs) -> GuarantorPriorityBoostPolicy:
        return GuarantorPriorityBoostPolicy(
            enabled=True,
            cost_points=30,
            daily_limit=2,
            used_today=0,
            remaining_today=2,
            cooldown_seconds=300,
            cooldown_remaining_seconds=0,
        )

    async def _fake_appeal_policy(*_args, **_kwargs) -> AppealPriorityBoostPolicy:
        return AppealPriorityBoostPolicy(
            enabled=False,
            cost_points=40,
            daily_limit=2,
            used_today=2,
            remaining_today=0,
            cooldown_seconds=300,
            cooldown_remaining_seconds=100,
        )

    monkeypatch.setattr("app.bot.handlers.start.upsert_user", _fake_upsert_user)
    monkeypatch.setattr("app.bot.handlers.start.enforce_callback_topic", _fake_enforce_callback_topic)
    monkeypatch.setattr("app.bot.handlers.start.get_user_points_summary", _fake_summary)
    monkeypatch.setattr("app.bot.handlers.start.list_user_points_entries", _fake_entries)
    monkeypatch.setattr("app.bot.handlers.start.get_feedback_priority_boost_policy", _fake_feedback_policy)
    monkeypatch.setattr("app.bot.handlers.start.get_guarantor_priority_boost_policy", _fake_guarantor_policy)
    monkeypatch.setattr("app.bot.handlers.start.get_appeal_priority_boost_policy", _fake_appeal_policy)

    await callback_dashboard_balance(cast(CallbackQuery, callback))

    assert callback.answers == [("", False)]
    assert message.edits
    assert "Текущий баланс: <b>120</b> points" in message.edits[0]
    assert "/boostfeedback" in message.edits[0]


def test_render_balance_text_contains_policy_and_recent_operations() -> None:
    text = _render_balance_text(
        summary=UserPointsSummary(balance=50, total_earned=80, total_spent=30, operations_count=2),
        feedback_policy=FeedbackPriorityBoostPolicy(
            enabled=True,
            cost_points=20,
            daily_limit=3,
            used_today=1,
            remaining_today=2,
            cooldown_seconds=60,
            cooldown_remaining_seconds=0,
        ),
        guarantor_policy=GuarantorPriorityBoostPolicy(
            enabled=True,
            cost_points=25,
            daily_limit=3,
            used_today=0,
            remaining_today=3,
            cooldown_seconds=60,
            cooldown_remaining_seconds=0,
        ),
        appeal_policy=AppealPriorityBoostPolicy(
            enabled=False,
            cost_points=30,
            daily_limit=1,
            used_today=1,
            remaining_today=0,
            cooldown_seconds=60,
            cooldown_remaining_seconds=60,
        ),
        recent_entries=[
            SimpleNamespace(
                created_at=datetime.now(UTC),
                amount=15,
                event_type=PointsEventType.FEEDBACK_APPROVED.value,
            )
        ],
    )

    assert "<b>Баланс и points</b>" in text
    assert "Текущий баланс: <b>50</b> points" in text
    assert "Буст фидбека" in text
    assert "Последние операции" in text


def test_balance_keyboard_has_settings_and_home_navigation() -> None:
    keyboard = _balance_keyboard()
    callback_data = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data is not None
    }
    assert "dash:settings" in callback_data
    assert "dash:home" in callback_data


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
        quiet_hours_enabled=False,
        quiet_hours_start_hour=23,
        quiet_hours_end_hour=8,
        quiet_hours_timezone="UTC",
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
        quiet_hours_enabled=True,
        quiet_hours_start_hour=23,
        quiet_hours_end_hour=8,
        quiet_hours_timezone="Europe/Moscow",
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
    assert "dash:settings:quiet:toggle" in callback_data
    assert "dash:settings:quiet:23-8" in callback_data
    assert "dash:settings:quiet:off" in callback_data
    assert "dash:settings:tz:UTC" in callback_data
    assert "dash:settings:tz:Europe/Moscow" in callback_data


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
