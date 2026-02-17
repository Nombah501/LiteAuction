from __future__ import annotations

from app.bot.keyboards.auction import open_auction_post_keyboard
from app.services.auction_service import resolve_auction_post_link


def test_resolve_auction_post_link_prefers_public_username() -> None:
    assert resolve_auction_post_link(-1001234567890, 17, "publicchat") == "https://t.me/publicchat/17"


def test_resolve_auction_post_link_uses_internal_chat_id() -> None:
    assert resolve_auction_post_link(-1001234567890, 42, None) == "https://t.me/c/1234567890/42"
    assert resolve_auction_post_link(-424242, 42, None) is None


def test_open_auction_post_keyboard_contains_single_url_button() -> None:
    keyboard = open_auction_post_keyboard("https://t.me/c/1234567890/42")

    assert len(keyboard.inline_keyboard) == 1
    assert len(keyboard.inline_keyboard[0]) == 1
    button = keyboard.inline_keyboard[0][0]
    assert button.text == "Открыть пост лота"
    assert button.url == "https://t.me/c/1234567890/42"
