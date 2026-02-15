from __future__ import annotations

from aiogram.types import InlineKeyboardButton

from app.bot.keyboards.auction import auction_active_keyboard, draft_publish_keyboard, photos_done_keyboard
from app.bot.keyboards.moderation import (
    complaint_actions_keyboard,
    moderation_complaints_list_keyboard,
    moderation_panel_keyboard,
)
from app.config import settings


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

    active = auction_active_keyboard("auc", min_step=2, has_buyout=True, photo_count=3)
    assert _button_by_callback(active.inline_keyboard, "bid:auc:1").icon_custom_emoji_id == "bid-x1"
    assert _button_by_callback(active.inline_keyboard, "bid:auc:3").icon_custom_emoji_id == "bid-x3"
    assert _button_by_callback(active.inline_keyboard, "bid:auc:5").icon_custom_emoji_id == "bid-x5"
    assert _button_by_callback(active.inline_keyboard, "gallery:auc").icon_custom_emoji_id == "gallery"

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
