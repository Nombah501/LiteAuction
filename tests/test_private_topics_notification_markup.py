from __future__ import annotations

from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.notification_policy_service import NotificationEventType
from app.services.private_topics_service import _notification_reply_markup


def test_notification_reply_markup_adds_mute_row_for_event() -> None:
    markup = _notification_reply_markup(
        reply_markup=None,
        notification_event=NotificationEventType.AUCTION_OUTBID,
        auction_id=None,
    )

    assert isinstance(markup, InlineKeyboardMarkup)
    assert len(markup.inline_keyboard) == 1
    button = markup.inline_keyboard[0][0]
    assert button.text == "Отключить этот тип"
    assert button.callback_data == "notif:mute:outbid"


def test_notification_reply_markup_appends_mute_row_to_existing_markup() -> None:
    existing = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Open", url="https://t.me/example/1")]]
    )

    markup = _notification_reply_markup(
        reply_markup=existing,
        notification_event=NotificationEventType.AUCTION_FINISH,
        auction_id=None,
    )

    assert isinstance(markup, InlineKeyboardMarkup)
    assert len(markup.inline_keyboard) == 2
    assert markup.inline_keyboard[0][0].text == "Open"
    assert markup.inline_keyboard[1][0].callback_data == "notif:mute:finish"


def test_notification_reply_markup_adds_auction_snooze_row_when_auction_known() -> None:
    markup = _notification_reply_markup(
        reply_markup=None,
        notification_event=NotificationEventType.AUCTION_WIN,
        auction_id=UUID("12345678-1234-5678-1234-567812345678"),
    )

    assert isinstance(markup, InlineKeyboardMarkup)
    assert len(markup.inline_keyboard) == 2
    assert markup.inline_keyboard[0][0].callback_data == "notif:snooze:12345678-1234-5678-1234-567812345678:60"
    assert markup.inline_keyboard[1][0].callback_data == "notif:mute:win"
