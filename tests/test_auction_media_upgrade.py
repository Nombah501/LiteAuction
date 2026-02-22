from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import cast

import pytest
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import EditMessageCaption, EditMessageMedia
from aiogram.types import InputMediaPhoto

from app.services import auction_service


@pytest.mark.asyncio
async def test_refresh_post_upgrades_text_message_to_media() -> None:
    calls: dict[str, object] = {"caption": 0, "media": 0}

    class _DummyBot:
        async def edit_message_caption(self, **kwargs):
            _ = kwargs
            calls["caption"] = int(calls["caption"]) + 1
            raise TelegramBadRequest(
                method=EditMessageCaption(chat_id=100, message_id=10, caption="updated"),
                message="Bad Request: there is no caption in the message to edit",
            )

        async def edit_message_media(self, **kwargs):
            calls["media"] = int(calls["media"]) + 1
            calls["media_kwargs"] = kwargs

    post = SimpleNamespace(id=7, inline_message_id=None, chat_id=100, message_id=10)

    await auction_service._refresh_auction_post_message(
        cast(Bot, _DummyBot()),
        post=post,
        caption="updated",
        reply_markup=None,
        photo_file_id="photo_file_id_1",
    )

    assert calls["caption"] == 1
    assert calls["media"] == 1
    media = cast(dict[str, object], calls["media_kwargs"])["media"]
    assert isinstance(media, InputMediaPhoto)
    assert media.media == "photo_file_id_1"
    assert media.caption == "updated"


@pytest.mark.asyncio
async def test_refresh_post_media_upgrade_is_idempotent_on_not_modified() -> None:
    calls = {"caption": 0, "media": 0}

    class _DummyBot:
        async def edit_message_caption(self, **kwargs):
            _ = kwargs
            calls["caption"] += 1
            raise TelegramBadRequest(
                method=EditMessageCaption(chat_id=100, message_id=10, caption="updated"),
                message="Bad Request: there is no caption in the message to edit",
            )

        async def edit_message_media(self, **kwargs):
            _ = kwargs
            calls["media"] += 1
            raise TelegramBadRequest(
                method=EditMessageMedia(chat_id=100, message_id=10, media=InputMediaPhoto(media="photo")),
                message="Bad Request: message is not modified",
            )

    post = SimpleNamespace(id=8, inline_message_id=None, chat_id=100, message_id=10)

    await auction_service._refresh_auction_post_message(
        cast(Bot, _DummyBot()),
        post=post,
        caption="updated",
        reply_markup=None,
        photo_file_id="photo_file_id_2",
    )
    await auction_service._refresh_auction_post_message(
        cast(Bot, _DummyBot()),
        post=post,
        caption="updated",
        reply_markup=None,
        photo_file_id="photo_file_id_2",
    )

    assert calls["caption"] == 2
    assert calls["media"] == 2


@pytest.mark.asyncio
async def test_refresh_post_does_not_upgrade_for_unrelated_caption_error() -> None:
    calls = {"caption": 0, "media": 0}

    class _DummyBot:
        async def edit_message_caption(self, **kwargs):
            _ = kwargs
            calls["caption"] += 1
            raise TelegramBadRequest(
                method=EditMessageCaption(chat_id=100, message_id=10, caption="updated"),
                message="Bad Request: chat not found",
            )

        async def edit_message_media(self, **kwargs):
            _ = kwargs
            calls["media"] += 1

    post = SimpleNamespace(id=9, inline_message_id=None, chat_id=100, message_id=10)

    await auction_service._refresh_auction_post_message(
        cast(Bot, _DummyBot()),
        post=post,
        caption="updated",
        reply_markup=None,
        photo_file_id="photo_file_id_3",
    )

    assert calls["caption"] == 1
    assert calls["media"] == 0


@pytest.mark.asyncio
async def test_refresh_post_logs_retry_after_and_stops(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = {"caption": 0, "media": 0}

    class _DummyBot:
        async def edit_message_caption(self, **kwargs):
            _ = kwargs
            calls["caption"] += 1
            raise TelegramRetryAfter(
                method=EditMessageCaption(chat_id=100, message_id=10, caption="updated"),
                message="Too Many Requests",
                retry_after=2,
            )

        async def edit_message_media(self, **kwargs):
            _ = kwargs
            calls["media"] += 1

    post = SimpleNamespace(id=10, inline_message_id=None, chat_id=100, message_id=10)
    caplog.set_level(logging.WARNING)

    await auction_service._refresh_auction_post_message(
        cast(Bot, _DummyBot()),
        post=post,
        caption="updated",
        reply_markup=None,
        photo_file_id="photo_file_id_4",
    )

    assert calls["caption"] == 1
    assert calls["media"] == 0
    assert "Rate limited while refreshing auction post 10" in caplog.text
    assert "retry_after=2" in caplog.text


@pytest.mark.asyncio
async def test_refresh_post_logs_transient_network_error_and_stops(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = {"caption": 0, "media": 0}

    class _DummyBot:
        async def edit_message_caption(self, **kwargs):
            _ = kwargs
            calls["caption"] += 1
            raise TelegramNetworkError(
                method=EditMessageCaption(chat_id=100, message_id=10, caption="updated"),
                message="timed out",
            )

        async def edit_message_media(self, **kwargs):
            _ = kwargs
            calls["media"] += 1

    post = SimpleNamespace(id=11, inline_message_id=None, chat_id=100, message_id=10)
    caplog.set_level(logging.WARNING)

    await auction_service._refresh_auction_post_message(
        cast(Bot, _DummyBot()),
        post=post,
        caption="updated",
        reply_markup=None,
        photo_file_id="photo_file_id_5",
    )

    assert calls["caption"] == 1
    assert calls["media"] == 0
    assert "Transient error while refreshing auction post 11" in caplog.text


@pytest.mark.asyncio
async def test_refresh_post_logs_forbidden_while_attaching_media(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = {"caption": 0, "media": 0}

    class _DummyBot:
        async def edit_message_caption(self, **kwargs):
            _ = kwargs
            calls["caption"] += 1
            raise TelegramBadRequest(
                method=EditMessageCaption(chat_id=100, message_id=10, caption="updated"),
                message="Bad Request: there is no caption in the message to edit",
            )

        async def edit_message_media(self, **kwargs):
            _ = kwargs
            calls["media"] += 1
            raise TelegramForbiddenError(
                method=EditMessageMedia(chat_id=100, message_id=10, media=InputMediaPhoto(media="photo")),
                message="Forbidden: bot was blocked by the user",
            )

    post = SimpleNamespace(id=12, inline_message_id=None, chat_id=100, message_id=10)
    caplog.set_level(logging.WARNING)

    await auction_service._refresh_auction_post_message(
        cast(Bot, _DummyBot()),
        post=post,
        caption="updated",
        reply_markup=None,
        photo_file_id="photo_file_id_6",
    )

    assert calls["caption"] == 1
    assert calls["media"] == 1
    assert "No rights to attach media for auction post 12" in caplog.text
