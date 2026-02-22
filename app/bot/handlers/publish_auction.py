from __future__ import annotations

import uuid

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import InputMediaPhoto, Message

from app.bot.keyboards.auction import auction_active_keyboard
from app.db.enums import AuctionStatus
from app.db.session import SessionFactory
from app.services.auction_service import (
    activate_auction_chat_post,
    load_auction_view,
    load_auction_photo_ids,
    parse_auction_uuid,
    refresh_auction_posts,
    render_auction_caption,
)
from app.services.publish_gate_service import evaluate_seller_publish_gate
from app.services.user_service import upsert_user

router = Router(name="publish_auction")


def _extract_publish_auction_id(text: str | None) -> uuid.UUID | None:
    raw = (text or "").strip()
    parts = raw.split(maxsplit=1)
    if len(parts) != 2:
        return None
    return parse_auction_uuid(parts[1].strip())


def _chunk_photo_ids(photo_ids: list[str], chunk_size: int = 10) -> list[list[str]]:
    if chunk_size <= 0:
        chunk_size = 10
    return [photo_ids[index : index + chunk_size] for index in range(0, len(photo_ids), chunk_size)]


async def _send_auction_album(
    bot: Bot,
    *,
    chat_id: int,
    message_thread_id: int | None,
    photo_ids: list[str],
) -> list[Message]:
    sent_messages: list[Message] = []
    for chunk_index, chunk in enumerate(_chunk_photo_ids(photo_ids, chunk_size=10)):
        media = [
            InputMediaPhoto(
                media=file_id,
                caption="Фото лота" if chunk_index == 0 and item_index == 0 else None,
            )
            for item_index, file_id in enumerate(chunk)
        ]
        try:
            if message_thread_id is not None:
                sent_chunk = await bot.send_media_group(
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    media=media,
                )
            else:
                sent_chunk = await bot.send_media_group(chat_id=chat_id, media=media)
        except (TelegramBadRequest, TelegramForbiddenError):
            await _safe_delete_messages(
                bot,
                chat_id=chat_id,
                message_ids=[album_message.message_id for album_message in sent_messages],
            )
            return []
        sent_messages.extend(sent_chunk)
    return sent_messages


async def _safe_delete_messages(bot: Bot, *, chat_id: int, message_ids: list[int]) -> None:
    for message_id in dict.fromkeys(message_ids):
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except (TelegramForbiddenError, TelegramBadRequest):
            continue


@router.message(Command("publish"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def publish_auction_to_current_chat(message: Message, bot: Bot) -> None:
    if message.from_user is None or message.chat is None:
        return

    auction_id = _extract_publish_auction_id(message.text)
    if auction_id is None:
        await message.answer("Формат: /publish <auction_id>")
        return

    publisher_user_id: int | None = None
    view = None
    photo_ids: list[str] = []
    async with SessionFactory() as session:
        async with session.begin():
            publisher = await upsert_user(session, message.from_user, mark_private_started=True)
            publisher_user_id = publisher.id
            view = await load_auction_view(session, auction_id)
            if view is None:
                await message.answer("Лот не найден")
                return

            if view.seller.id != publisher.id:
                await message.answer("Опубликовать лот может только его продавец")
                return

            if view.auction.status != AuctionStatus.DRAFT:
                await message.answer("Этот лот уже опубликован или больше не доступен для публикации")
                return

            publish_gate = await evaluate_seller_publish_gate(session, seller_user_id=publisher.id)
            if not publish_gate.allowed:
                await message.answer(publish_gate.block_message or "Публикация временно ограничена")
                return

            photo_ids = await load_auction_photo_ids(session, auction_id)
            if not photo_ids:
                photo_ids = [view.auction.photo_file_id]

    if view is None or publisher_user_id is None:
        return

    album_message_ids: list[int] = []
    if len(photo_ids) > 1:
        album_messages = await _send_auction_album(
            bot,
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            photo_ids=photo_ids,
        )
        album_message_ids = [album_message.message_id for album_message in album_messages]

    try:
        sent_message = await bot.send_photo(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            photo=view.auction.photo_file_id,
            caption=render_auction_caption(view, publish_pending=True),
            reply_markup=auction_active_keyboard(
                auction_id=str(view.auction.id),
                min_step=view.auction.min_step,
                has_buyout=view.auction.buyout_price is not None,
            ),
        )
    except TelegramForbiddenError:
        if album_message_ids:
            await _safe_delete_messages(bot, chat_id=message.chat.id, message_ids=album_message_ids)
        await message.answer("Бот не может публиковать в этом чате/разделе. Проверьте права бота.")
        return
    except TelegramBadRequest:
        if album_message_ids:
            await _safe_delete_messages(bot, chat_id=message.chat.id, message_ids=album_message_ids)
        await message.answer("Не удалось опубликовать лот в этом чате/разделе")
        return

    activated = None
    try:
        async with SessionFactory() as session:
            async with session.begin():
                activated = await activate_auction_chat_post(
                    session,
                    auction_id=auction_id,
                    publisher_user_id=publisher_user_id,
                    chat_id=sent_message.chat.id,
                    message_id=sent_message.message_id,
                )
    except Exception:
        await _safe_delete_messages(
            bot,
            chat_id=sent_message.chat.id,
            message_ids=[sent_message.message_id, *album_message_ids],
        )
        raise

    if activated is None:
        await _safe_delete_messages(
            bot,
            chat_id=sent_message.chat.id,
            message_ids=[sent_message.message_id, *album_message_ids],
        )
        await message.answer("Лот уже был опубликован. Обновите статус в личном чате с ботом.")
        return

    await refresh_auction_posts(bot, auction_id)
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except TelegramForbiddenError:
        pass
    except TelegramBadRequest:
        pass
