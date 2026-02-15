from __future__ import annotations

import uuid

from app.bot.handlers.publish_auction import _chunk_photo_ids, _extract_publish_auction_id
from app.bot.keyboards.auction import draft_publish_keyboard


def test_extract_publish_auction_id_parses_uuid() -> None:
    auction_id = uuid.uuid4()
    parsed = _extract_publish_auction_id(f"/publish {auction_id}")
    assert parsed == auction_id


def test_extract_publish_auction_id_rejects_invalid_input() -> None:
    assert _extract_publish_auction_id("/publish") is None
    assert _extract_publish_auction_id("/publish not-a-uuid") is None
    assert _extract_publish_auction_id(None) is None


def test_draft_publish_keyboard_contains_copy_publish_button() -> None:
    auction_id = str(uuid.uuid4())
    keyboard = draft_publish_keyboard(auction_id, photo_count=3)
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert any(
        button.copy_text is not None and button.copy_text.text == f"/publish {auction_id}"
        for button in buttons
    )


def test_chunk_photo_ids_batches_by_ten() -> None:
    photo_ids = [f"photo-{index}" for index in range(23)]
    chunks = _chunk_photo_ids(photo_ids, chunk_size=10)

    assert len(chunks) == 3
    assert len(chunks[0]) == 10
    assert len(chunks[1]) == 10
    assert len(chunks[2]) == 3
    assert chunks[0][0] == "photo-0"
    assert chunks[2][-1] == "photo-22"
