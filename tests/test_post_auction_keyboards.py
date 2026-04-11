from __future__ import annotations

from app.bot.keyboards.auction import (
    deal_completion_keyboard,
    deal_topic_keyboard,
    moderation_completion_keyboard,
    no_bids_keyboard,
)


def test_deal_completion_keyboard_seller() -> None:
    kb = deal_completion_keyboard(
        auction_id="a1b2c3d4",
        post_url="https://t.me/channel/123",
        is_seller=True,
    )
    buttons = kb.inline_keyboard
    assert len(buttons) == 3
    assert "deal:write:a1b2c3d4" == buttons[0][0].callback_data
    assert "победителю" in buttons[0][0].text.lower()


def test_deal_completion_keyboard_winner() -> None:
    kb = deal_completion_keyboard(
        auction_id="a1b2c3d4",
        post_url="https://t.me/channel/123",
        is_seller=False,
    )
    buttons = kb.inline_keyboard
    assert len(buttons) == 3
    assert "продавцу" in buttons[0][0].text.lower()


def test_deal_completion_keyboard_no_post_url() -> None:
    kb = deal_completion_keyboard(
        auction_id="a1b2c3d4",
        post_url=None,
        is_seller=True,
    )
    buttons = kb.inline_keyboard
    assert len(buttons) == 2


def test_no_bids_keyboard() -> None:
    kb = no_bids_keyboard(
        auction_id="a1b2c3d4",
        post_url="https://t.me/channel/123",
    )
    buttons = kb.inline_keyboard
    assert len(buttons) == 2
    assert "deal:republish:a1b2c3d4" == buttons[0][0].callback_data


def test_no_bids_keyboard_no_post_url() -> None:
    kb = no_bids_keyboard(
        auction_id="a1b2c3d4",
        post_url=None,
    )
    buttons = kb.inline_keyboard
    assert len(buttons) == 1


def test_deal_topic_keyboard() -> None:
    kb = deal_topic_keyboard(auction_id="a1b2c3d4")
    buttons = kb.inline_keyboard
    assert len(buttons) == 2
    all_callbacks = [b.callback_data for row in buttons for b in row]
    assert "deal:guarant:a1b2c3d4" in all_callbacks
    assert "deal:feedback:a1b2c3d4" in all_callbacks
    assert "deal:complaint:a1b2c3d4" in all_callbacks


def test_moderation_completion_keyboard() -> None:
    kb = moderation_completion_keyboard(
        auction_id="a1b2c3d4",
        post_url="https://t.me/channel/123",
    )
    buttons = kb.inline_keyboard
    assert len(buttons) == 2
