from __future__ import annotations

from aiogram.types import InlineKeyboardButton

from app.bot.keyboards.auction import (
    auction_active_keyboard,
    draft_publish_keyboard,
    my_auction_detail_keyboard,
    my_auction_subview_keyboard,
    my_auctions_list_keyboard,
    photos_done_keyboard,
    start_private_keyboard,
)
from app.bot.keyboards.moderation import (
    complaint_actions_keyboard,
    moderation_complaints_list_keyboard,
    moderation_panel_keyboard,
)
from app.config import settings
from app.db.enums import AuctionStatus


def _all_buttons(rows: list[list[InlineKeyboardButton]]) -> list[InlineKeyboardButton]:
    return [button for row in rows for button in row]


def _button_by_callback(rows: list[list[InlineKeyboardButton]], callback_data: str) -> InlineKeyboardButton:
    for button in _all_buttons(rows):
        if button.callback_data == callback_data:
            return button
    raise AssertionError(f"Button not found: {callback_data}")


def _button_by_text(rows: list[list[InlineKeyboardButton]], text: str) -> InlineKeyboardButton:
    for button in _all_buttons(rows):
        if button.text == text:
            return button
    raise AssertionError(f"Button not found: {text}")


def _button_by_url(rows: list[list[InlineKeyboardButton]], url: str) -> InlineKeyboardButton:
    for button in _all_buttons(rows):
        if button.url == url:
            return button
    raise AssertionError(f"Button not found by url: {url}")


def test_auction_keyboard_uses_granular_emoji_ids(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ui_emoji_bid_id", "bid-base")
    monkeypatch.setattr(settings, "ui_emoji_bid_x1_id", "bid-x1")
    monkeypatch.setattr(settings, "ui_emoji_bid_x3_id", "bid-x3")
    monkeypatch.setattr(settings, "ui_emoji_bid_x5_id", "bid-x5")
    monkeypatch.setattr(settings, "ui_emoji_publish_id", "publish-base")
    monkeypatch.setattr(settings, "ui_emoji_copy_publish_id", "copy-publish")
    monkeypatch.setattr(settings, "ui_emoji_gallery_id", "gallery")
    monkeypatch.setattr(settings, "ui_emoji_create_auction_id", "create-base")
    monkeypatch.setattr(settings, "ui_emoji_new_lot_id", "new-lot")
    monkeypatch.setattr(settings, "ui_emoji_photos_done_id", "photos-done")
    monkeypatch.setattr(settings, "bot_username", "liteauctionbot")

    active = auction_active_keyboard("auc", min_step=2, has_buyout=True, photo_count=3)
    assert _button_by_callback(active.inline_keyboard, "bid:auc:1").icon_custom_emoji_id == "bid-x1"
    assert _button_by_callback(active.inline_keyboard, "bid:auc:3").icon_custom_emoji_id == "bid-x3"
    assert _button_by_callback(active.inline_keyboard, "bid:auc:5").icon_custom_emoji_id == "bid-x5"
    assert _button_by_callback(active.inline_keyboard, "gallery:auc").icon_custom_emoji_id == "gallery"
    assert _button_by_callback(active.inline_keyboard, "bid:auc:1").text == "+$2"
    assert _button_by_callback(active.inline_keyboard, "bid:auc:3").text == "+$6"
    assert _button_by_callback(active.inline_keyboard, "bid:auc:5").text == "+$10"
    assert _button_by_callback(active.inline_keyboard, "bid:auc:1").style == "success"
    assert _button_by_callback(active.inline_keyboard, "bid:auc:3").style == "success"
    assert _button_by_callback(active.inline_keyboard, "bid:auc:5").style == "success"
    assert _button_by_callback(active.inline_keyboard, "buy:auc").text == "Выкуп"
    assert _button_by_callback(active.inline_keyboard, "report:auc").text == "Жалоба"
    assert _button_by_callback(active.inline_keyboard, "gallery:auc").text == "Фото 3"
    bot_button = _button_by_url(active.inline_keyboard, "https://t.me/liteauctionbot?start=auction_gate")
    assert bot_button.text == "Бот"

    utility_rows = [
        row for row in active.inline_keyboard if any(button.callback_data == "gallery:auc" for button in row)
    ]
    assert len(utility_rows) == 1
    assert [button.text for button in utility_rows[0]] == ["Фото 3", "Жалоба", "Бот"]

    draft = draft_publish_keyboard("auc", photo_count=3)
    assert _button_by_text(draft.inline_keyboard, "Скопировать /publish").icon_custom_emoji_id == "copy-publish"
    assert _button_by_text(draft.inline_keyboard, "Создать новый лот").icon_custom_emoji_id == "new-lot"

    done = photos_done_keyboard()
    assert _button_by_text(done.inline_keyboard, "Готово").icon_custom_emoji_id == "photos-done"


def test_auction_keyboard_emoji_fallbacks(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ui_emoji_bid_id", "bid-base")
    monkeypatch.setattr(settings, "ui_emoji_bid_x1_id", "")
    monkeypatch.setattr(settings, "ui_emoji_bid_x3_id", "")
    monkeypatch.setattr(settings, "ui_emoji_bid_x5_id", "")
    monkeypatch.setattr(settings, "ui_emoji_publish_id", "publish-base")
    monkeypatch.setattr(settings, "ui_emoji_copy_publish_id", "")
    monkeypatch.setattr(settings, "ui_emoji_gallery_id", "")
    monkeypatch.setattr(settings, "ui_emoji_create_auction_id", "create-base")
    monkeypatch.setattr(settings, "ui_emoji_new_lot_id", "")
    monkeypatch.setattr(settings, "ui_emoji_photos_done_id", "")
    monkeypatch.setattr(settings, "bot_username", "")

    active = auction_active_keyboard("auc", min_step=2, has_buyout=False, photo_count=2)
    assert _button_by_callback(active.inline_keyboard, "bid:auc:1").icon_custom_emoji_id == "bid-base"
    assert _button_by_callback(active.inline_keyboard, "bid:auc:3").icon_custom_emoji_id == "bid-base"
    assert _button_by_callback(active.inline_keyboard, "bid:auc:5").icon_custom_emoji_id == "bid-base"
    assert _button_by_callback(active.inline_keyboard, "gallery:auc").icon_custom_emoji_id == "publish-base"

    draft = draft_publish_keyboard("auc", photo_count=2)
    assert _button_by_text(draft.inline_keyboard, "Скопировать /publish").icon_custom_emoji_id == "publish-base"
    assert _button_by_text(draft.inline_keyboard, "Создать новый лот").icon_custom_emoji_id == "create-base"

    done = photos_done_keyboard()
    assert _button_by_text(done.inline_keyboard, "Готово").icon_custom_emoji_id == "create-base"


def test_start_private_keyboard_regular_user_order() -> None:
    keyboard = start_private_keyboard(show_moderation_button=False)

    assert [row[0].text for row in keyboard.inline_keyboard] == [
        "Создать аукцион",
        "Мои аукционы",
        "Настройки",
        "Баланс",
    ]
    assert all(button.callback_data != "mod:panel" for button in _all_buttons(keyboard.inline_keyboard))


def test_start_private_keyboard_moderator_has_mod_button_last(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ui_emoji_mod_panel_id", "mod-panel")
    keyboard = start_private_keyboard(show_moderation_button=True)

    assert [row[0].text for row in keyboard.inline_keyboard] == [
        "Создать аукцион",
        "Мои аукционы",
        "Настройки",
        "Баланс",
        "Мод-панель",
    ]
    mod_button = _button_by_callback(keyboard.inline_keyboard, "mod:panel")
    assert mod_button.style == "success"
    assert mod_button.icon_custom_emoji_id == "mod-panel"


def test_my_auctions_list_keyboard_structure() -> None:
    keyboard = my_auctions_list_keyboard(
        auctions=[
            ("12345678-1234-5678-1234-567812345678", "#12345678 · Активен · $95"),
            ("87654321-4321-8765-4321-876543218765", "#87654321 · Черновик · $40"),
        ],
        current_filter="a",
        current_sort="n",
        page=1,
        has_prev=True,
        has_next=False,
    )

    assert _button_by_callback(keyboard.inline_keyboard, "dash:my:list:a:n:0").text == "[Активные]"
    assert _button_by_callback(keyboard.inline_keyboard, "dash:my:list:f:n:0").text == "Завершенные"
    assert _button_by_callback(keyboard.inline_keyboard, "dash:my:list:a:n:0").text == "[Активные]"
    assert _button_by_callback(keyboard.inline_keyboard, "dash:my:list:a:e:0").text == "Скоро финиш"
    assert _button_by_callback(keyboard.inline_keyboard, "dash:my:list:a:b:0").text == "Больше ставок"
    assert _button_by_callback(
        keyboard.inline_keyboard,
        "dash:my:view:12345678-1234-5678-1234-567812345678:a:n:1",
    ).text == "#12345678 · Активен · $95"
    assert _button_by_callback(keyboard.inline_keyboard, "dash:my:list:a:n:1").text == "Стр. 2"


def test_my_auction_detail_and_subview_keyboards() -> None:
    detail = my_auction_detail_keyboard(
        auction_id="12345678-1234-5678-1234-567812345678",
        filter_key="f",
        sort_key="e",
        page=2,
        status=AuctionStatus.ACTIVE,
        first_post_url="https://t.me/c/123/456",
    )

    assert _button_by_callback(
        detail.inline_keyboard,
        "dash:my:bids:12345678-1234-5678-1234-567812345678:f:e:2",
    ).text == "Ставки"
    assert _button_by_callback(
        detail.inline_keyboard,
        "dash:my:posts:12345678-1234-5678-1234-567812345678:f:e:2",
    ).text == "Посты"
    assert _button_by_callback(
        detail.inline_keyboard,
        "dash:my:refresh:12345678-1234-5678-1234-567812345678:f:e:2",
    ).text == "Обновить посты"
    assert _button_by_callback(detail.inline_keyboard, "gallery:12345678-1234-5678-1234-567812345678").text == "Фото"
    assert _button_by_url(detail.inline_keyboard, "https://t.me/c/123/456").text == "Открыть пост"

    subview = my_auction_subview_keyboard(
        auction_id="12345678-1234-5678-1234-567812345678",
        filter_key="l",
        sort_key="b",
        page=0,
    )
    assert _button_by_callback(
        subview.inline_keyboard,
        "dash:my:view:12345678-1234-5678-1234-567812345678:l:b:0",
    ).text == "Карточка лота"


def test_my_auction_detail_keyboard_draft_has_publish_actions() -> None:
    detail = my_auction_detail_keyboard(
        auction_id="12345678-1234-5678-1234-567812345678",
        filter_key="d",
        sort_key="n",
        page=0,
        status=AuctionStatus.DRAFT,
        first_post_url=None,
    )

    inline_button = _button_by_text(detail.inline_keyboard, "Inline пост")
    assert inline_button.switch_inline_query == "auc_12345678-1234-5678-1234-567812345678"
    publish_copy_button = _button_by_text(detail.inline_keyboard, "Скопировать /publish")
    assert publish_copy_button.copy_text is not None
    assert publish_copy_button.copy_text.text == "/publish 12345678-1234-5678-1234-567812345678"


def test_moderation_keyboard_uses_granular_emoji_ids(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ui_emoji_mod_panel_id", "mod-base")
    monkeypatch.setattr(settings, "ui_emoji_mod_complaints_id", "mod-complaints")
    monkeypatch.setattr(settings, "ui_emoji_mod_signals_id", "mod-signals")
    monkeypatch.setattr(settings, "ui_emoji_mod_frozen_id", "mod-frozen")
    monkeypatch.setattr(settings, "ui_emoji_mod_appeals_id", "mod-appeals")
    monkeypatch.setattr(settings, "ui_emoji_mod_stats_id", "mod-stats")
    monkeypatch.setattr(settings, "ui_emoji_mod_refresh_id", "mod-refresh")
    monkeypatch.setattr(settings, "ui_emoji_mod_prev_id", "mod-prev")
    monkeypatch.setattr(settings, "ui_emoji_mod_menu_id", "mod-menu")
    monkeypatch.setattr(settings, "ui_emoji_mod_next_id", "mod-next")

    panel = moderation_panel_keyboard()
    assert _button_by_callback(panel.inline_keyboard, "modui:complaints:0").icon_custom_emoji_id == "mod-complaints"
    assert _button_by_callback(panel.inline_keyboard, "modui:signals:0").icon_custom_emoji_id == "mod-signals"
    assert _button_by_callback(panel.inline_keyboard, "modui:frozen:0").icon_custom_emoji_id == "mod-frozen"
    assert _button_by_callback(panel.inline_keyboard, "modui:appeals:0").icon_custom_emoji_id == "mod-appeals"
    assert _button_by_callback(panel.inline_keyboard, "modui:stats").icon_custom_emoji_id == "mod-stats"
    assert _button_by_callback(panel.inline_keyboard, "modui:home").icon_custom_emoji_id == "mod-refresh"

    list_kb = moderation_complaints_list_keyboard(items=[(1, "c1")], page=1, has_next=True)
    assert _button_by_callback(list_kb.inline_keyboard, "modui:complaints:0").icon_custom_emoji_id == "mod-prev"
    assert _button_by_callback(list_kb.inline_keyboard, "modui:home").icon_custom_emoji_id == "mod-menu"
    assert _button_by_callback(list_kb.inline_keyboard, "modui:complaints:2").icon_custom_emoji_id == "mod-next"


def test_moderation_keyboard_emoji_fallbacks(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ui_emoji_mod_panel_id", "mod-base")
    monkeypatch.setattr(settings, "ui_emoji_mod_freeze_id", "")
    monkeypatch.setattr(settings, "ui_emoji_mod_remove_top_id", "")
    monkeypatch.setattr(settings, "ui_emoji_mod_ban_id", "")
    monkeypatch.setattr(settings, "ui_emoji_mod_reject_id", "")
    monkeypatch.setattr(settings, "ui_emoji_mod_back_id", "")

    actions = complaint_actions_keyboard(10, back_callback="back:1")
    assert _button_by_callback(actions.inline_keyboard, "modrep:freeze:10").icon_custom_emoji_id == "mod-base"
    assert _button_by_callback(actions.inline_keyboard, "modrep:rm_top:10").icon_custom_emoji_id == "mod-base"
    assert _button_by_callback(actions.inline_keyboard, "modrep:ban_top:10").icon_custom_emoji_id == "mod-base"
    assert _button_by_callback(actions.inline_keyboard, "modrep:dismiss:10").icon_custom_emoji_id == "mod-base"
    assert _button_by_callback(actions.inline_keyboard, "back:1").icon_custom_emoji_id == "mod-base"
