from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from aiogram.types import CallbackQuery, ErrorEvent, Message

from app.bot.handlers.error_boundary import _extract_error_context, handle_bot_error


class _DummyCallback:
    def __init__(self) -> None:
        self.from_user = SimpleNamespace(id=501)
        self.data = "bid:test"
        self.message = cast(Message, SimpleNamespace(chat=SimpleNamespace(id=42)))
        self.answers: list[tuple[str, bool]] = []

    async def answer(self, text: str = "", show_alert: bool = False, **_kwargs) -> None:
        self.answers.append((text, show_alert))


class _DummyMessage:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(id=77)
        self.from_user = SimpleNamespace(id=902)
        self.text = "/start"
        self.answers: list[str] = []

    async def answer(self, text: str, **_kwargs) -> None:
        self.answers.append(text)


@pytest.mark.asyncio
async def test_handle_bot_error_answers_callback_alert() -> None:
    callback = _DummyCallback()
    event = cast(
        ErrorEvent,
        SimpleNamespace(
            update=SimpleNamespace(callback_query=cast(CallbackQuery, callback), message=None),
            exception=RuntimeError("boom"),
        ),
    )

    handled = await handle_bot_error(event)

    assert handled is True
    assert callback.answers == [("Произошла ошибка. Повторите действие позже.", True)]


@pytest.mark.asyncio
async def test_handle_bot_error_answers_message() -> None:
    message = _DummyMessage()
    event = cast(
        ErrorEvent,
        SimpleNamespace(
            update=SimpleNamespace(callback_query=None, message=cast(Message, message)),
            exception=RuntimeError("boom"),
        ),
    )

    handled = await handle_bot_error(event)

    assert handled is True
    assert message.answers == ["Произошла ошибка. Попробуйте еще раз."]


@pytest.mark.asyncio
async def test_handle_bot_error_reraises_cancelled_error() -> None:
    event = cast(
        ErrorEvent,
        SimpleNamespace(
            update=SimpleNamespace(callback_query=None, message=None),
            exception=asyncio.CancelledError(),
        ),
    )

    with pytest.raises(asyncio.CancelledError):
        await handle_bot_error(event)


def test_extract_error_context_for_callback() -> None:
    callback = _DummyCallback()
    event = cast(
        ErrorEvent,
        SimpleNamespace(
            update=SimpleNamespace(callback_query=cast(CallbackQuery, callback), message=None),
            exception=RuntimeError("boom"),
        ),
    )

    context = _extract_error_context(event)

    assert context["update_type"] == "callback_query"
    assert context["user_id"] == 501
    assert context["callback_data"] == "bid:test"
