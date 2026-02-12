from __future__ import annotations

import logging
from enum import StrEnum

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup, Message

from app.config import settings

logger = logging.getLogger(__name__)


class ModerationTopicSection(StrEnum):
    COMPLAINTS = "complaints"
    SUGGESTIONS = "suggestions"
    BUGS = "bugs"
    GUARANTORS = "guarantors"
    APPEALS = "appeals"
    AUCTIONS_ACTIVE = "auctions_active"
    AUCTIONS_FROZEN = "auctions_frozen"
    AUCTIONS_CLOSED = "auctions_closed"


def resolve_topic_thread_id(section: ModerationTopicSection | str) -> int | None:
    section_key = str(section).strip().lower()
    if section_key:
        section_thread_id = settings.parsed_moderation_topic_id(section_key)
        if section_thread_id is not None:
            return section_thread_id
    return settings.parsed_moderation_thread_id()


def _extract_message_ref(message: Message | None) -> tuple[int, int] | None:
    if message is None or message.chat is None:
        return None
    return message.chat.id, message.message_id


async def _send_message(
    bot: Bot,
    *,
    chat_id: int,
    text: str,
    thread_id: int | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    if thread_id is not None and reply_markup is not None:
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            message_thread_id=thread_id,
            reply_markup=reply_markup,
        )
    if thread_id is not None:
        return await bot.send_message(chat_id=chat_id, text=text, message_thread_id=thread_id)
    if reply_markup is not None:
        return await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    return await bot.send_message(chat_id=chat_id, text=text)


async def send_section_message(
    bot: Bot,
    *,
    section: ModerationTopicSection | str,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> tuple[int, int] | None:
    moderation_chat_id = settings.parsed_moderation_chat_id()
    thread_id = resolve_topic_thread_id(section)

    if moderation_chat_id is not None:
        try:
            moderation_message = await _send_message(
                bot,
                chat_id=moderation_chat_id,
                text=text,
                thread_id=thread_id,
                reply_markup=reply_markup,
            )
            return _extract_message_ref(moderation_message)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.warning("Failed to send moderation section message (%s): %s", section, exc)

    first_admin_ref: tuple[int, int] | None = None
    for admin_tg_id in settings.parsed_admin_user_ids():
        try:
            admin_message = await _send_message(
                bot,
                chat_id=admin_tg_id,
                text=text,
                reply_markup=reply_markup,
            )
            if first_admin_ref is None:
                first_admin_ref = _extract_message_ref(admin_message)
        except (TelegramBadRequest, TelegramForbiddenError):
            continue

    return first_admin_ref
