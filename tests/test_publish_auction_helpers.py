from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendPhoto

from app.bot.handlers.publish_auction import (
    _chunk_photo_ids,
    _extract_publish_auction_id,
    _safe_delete_messages,
    _send_auction_album,
)
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


@pytest.mark.asyncio
async def test_send_auction_album_cleans_partial_chunks_on_failure() -> None:
    class _DummyBot:
        def __init__(self) -> None:
            self.send_calls = 0
            self.deleted_messages: list[tuple[int, int]] = []

        async def send_media_group(self, *, chat_id: int, media, message_thread_id: int | None = None):
            _ = media
            _ = message_thread_id
            self.send_calls += 1
            if self.send_calls == 1:
                return [
                    SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=101),
                    SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=102),
                ]
            raise TelegramBadRequest(
                method=SendPhoto(chat_id=chat_id, photo="photo-x"),
                message="Bad Request: album chunk failed",
            )

        async def delete_message(self, *, chat_id: int, message_id: int) -> None:
            self.deleted_messages.append((chat_id, message_id))

    bot = _DummyBot()
    messages = await _send_auction_album(
        bot,
        chat_id=-100777,
        message_thread_id=None,
        photo_ids=[f"photo-{idx}" for idx in range(12)],
    )

    assert messages == []
    assert bot.deleted_messages == [(-100777, 101), (-100777, 102)]


@pytest.mark.asyncio
async def test_safe_delete_messages_deduplicates_ids() -> None:
    class _DummyBot:
        def __init__(self) -> None:
            self.deleted_messages: list[tuple[int, int]] = []

        async def delete_message(self, *, chat_id: int, message_id: int) -> None:
            self.deleted_messages.append((chat_id, message_id))

    bot = _DummyBot()
    await _safe_delete_messages(bot, chat_id=-100900, message_ids=[201, 201, 202])

    assert bot.deleted_messages == [(-100900, 201), (-100900, 202)]
