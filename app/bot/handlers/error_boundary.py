from __future__ import annotations

import asyncio
import logging

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, ErrorEvent, Message

logger = logging.getLogger(__name__)
router = Router(name="error_boundary")


def _extract_error_context(event: ErrorEvent) -> dict[str, str | int | None]:
    update = event.update
    callback: CallbackQuery | None = getattr(update, "callback_query", None)
    message: Message | None = getattr(update, "message", None)

    if callback is not None:
        callback_message = callback.message if isinstance(callback.message, Message) else None
        chat_id = (
            callback_message.chat.id
            if callback_message is not None and callback_message.chat is not None
            else None
        )
        return {
            "update_type": "callback_query",
            "chat_id": chat_id,
            "user_id": callback.from_user.id if callback.from_user is not None else None,
            "callback_data": callback.data,
            "message_text": None,
        }

    if message is not None:
        return {
            "update_type": "message",
            "chat_id": message.chat.id if message.chat is not None else None,
            "user_id": message.from_user.id if message.from_user is not None else None,
            "callback_data": None,
            "message_text": message.text,
        }

    return {
        "update_type": "unknown",
        "chat_id": None,
        "user_id": None,
        "callback_data": None,
        "message_text": None,
    }


@router.error()
async def handle_bot_error(event: ErrorEvent) -> bool:
    if isinstance(event.exception, asyncio.CancelledError):
        raise event.exception

    context = _extract_error_context(event)
    logger.error(
        "bot_handler_unhandled_exception update_type=%s chat_id=%s user_id=%s callback_data=%s message_text=%s",
        context["update_type"],
        context["chat_id"],
        context["user_id"],
        context["callback_data"],
        context["message_text"],
        exc_info=(
            type(event.exception),
            event.exception,
            event.exception.__traceback__,
        ),
    )

    callback: CallbackQuery | None = getattr(event.update, "callback_query", None)
    if callback is not None:
        try:
            await callback.answer("Произошла ошибка. Повторите действие позже.", show_alert=True)
        except TelegramAPIError:
            pass
        return True

    message: Message | None = getattr(event.update, "message", None)
    if message is not None:
        try:
            await message.answer("Произошла ошибка. Попробуйте еще раз.")
        except TelegramAPIError:
            pass
    return True
