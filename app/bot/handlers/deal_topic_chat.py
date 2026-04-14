from __future__ import annotations

import uuid

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.bot.keyboards.auction import styled_button
from app.db.enums import DealTopicStatus
from app.db.models import Auction, DealTopic, User
from app.db.session import SessionFactory
from app.services.deal_topic_service import close_deal_topic, forward_deal_message
from app.services.notification_copy_service import format_user_mention

router = Router(name="deal_topic_chat")


def parse_close_payload(data: str) -> uuid.UUID | None:
    parts = data.split(":")
    if len(parts) != 3 or parts[1] != "close":
        return None
    try:
        return uuid.UUID(parts[2])
    except ValueError:
        return None


@router.message(F.chat.type == "private", F.message_thread_id)
async def handle_deal_topic_message(message: Message, bot: Bot) -> None:
    if message.from_user is None or message.message_thread_id is None:
        return
    if message.is_automatic_forward:
        return
    if message.from_user.is_bot:
        return

    thread_id = message.message_thread_id

    async with SessionFactory() as session:
        deal = await session.scalar(
            select(DealTopic).where(
                DealTopic.seller_topic_id == thread_id,
                DealTopic.status == DealTopicStatus.ACTIVE,
            )
        )

        if deal is None:
            deal = await session.scalar(
                select(DealTopic).where(
                    DealTopic.winner_topic_id == thread_id,
                    DealTopic.status == DealTopicStatus.ACTIVE,
                )
            )

        if deal is None:
            return

        is_seller_thread = deal.seller_topic_id == thread_id

        auction = await session.scalar(
            select(Auction).where(Auction.id == deal.auction_id)
        )
        if auction is None:
            return

        seller = await session.scalar(
            select(User).where(User.id == auction.seller_user_id)
        )
        winner = (
            await session.scalar(select(User).where(User.id == auction.winner_user_id))
            if auction.winner_user_id
            else None
        )

        if is_seller_thread:
            recipient_tg_id = winner.tg_user_id if winner else None
            recipient_topic_id = deal.winner_topic_id
            sender_label = format_user_mention(
                username=seller.username if seller else None,
                first_name=seller.first_name if seller else None,
                tg_user_id=seller.tg_user_id if seller else 0,
            )
        else:
            recipient_tg_id = seller.tg_user_id if seller else None
            recipient_topic_id = deal.seller_topic_id
            sender_label = format_user_mention(
                username=winner.username if winner else None,
                first_name=winner.first_name if winner else None,
                tg_user_id=winner.tg_user_id if winner else 0,
            )

        if recipient_tg_id is None or recipient_topic_id is None:
            await message.answer("Контрагент не найден.")
            return

    if message.document:
        try:
            await bot.send_document(
                chat_id=recipient_tg_id,
                message_thread_id=recipient_topic_id,
                document=message.document.file_id,
                caption=f"<b>{sender_label}:</b>\n{message.document.file_name or 'Документ'}",
                parse_mode="HTML",
            )
        except Exception:
            await message.answer("Не удалось переслать документ.")
        return

    if message.voice:
        try:
            await bot.send_voice(
                chat_id=recipient_tg_id,
                message_thread_id=recipient_topic_id,
                voice=message.voice.file_id,
                caption=f"<b>{sender_label}:</b>",
                parse_mode="HTML",
            )
        except Exception:
            await message.answer("Не удалось переслать голосовое.")
        return

    photo_file_id = None
    text = None
    if message.photo:
        photo_file_id = message.photo[-1].file_id
        text = message.caption
    elif message.text:
        text = message.text
    else:
        await message.answer("Этот тип сообщения не поддерживается.")
        return

    await forward_deal_message(
        bot,
        deal=deal,
        sender_label=sender_label,
        recipient_tg_id=recipient_tg_id,
        recipient_topic_id=recipient_topic_id,
        text=text,
        photo_file_id=photo_file_id,
    )


@router.callback_query(F.data.startswith("deal:closerequest:"))
async def handle_deal_close_request(callback: CallbackQuery) -> None:
    auction_id_str = callback.data.split(":")[-1]
    short_id = auction_id_str[:8]

    confirm_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                styled_button(text="✅ Да, закрыть", callback_data=f"deal:close:{auction_id_str}"),
                styled_button(text="❌ Отмена", callback_data="deal:closecancel"),
            ]
        ]
    )
    await callback.message.answer(
        f"Вы уверены, что хотите закрыть сделку #{short_id}?",
        reply_markup=confirm_kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("deal:close:"))
async def handle_deal_close(callback: CallbackQuery, bot: Bot) -> None:
    if callback.from_user is None:
        return

    auction_id = parse_close_payload(callback.data)
    if auction_id is None:
        await callback.answer("Некорректный запрос", show_alert=True)
        return

    async with SessionFactory() as session:
        async with session.begin():
            deal = await session.scalar(
                select(DealTopic).where(
                    DealTopic.auction_id == auction_id,
                    DealTopic.status == DealTopicStatus.ACTIVE,
                )
            )
            if deal is None:
                await callback.answer("Топик сделки не найден или уже закрыт", show_alert=True)
                return

            auction = await session.scalar(
                select(Auction).where(Auction.id == auction_id)
            )

            await close_deal_topic(session, deal=deal)

            short_id = str(auction_id)[:8]
            close_text = f"✅ Сделка #{short_id} закрыта.\nНе забудьте оставить отзыв — /tradefeedback"

            if auction:
                seller = await session.scalar(
                    select(User).where(User.id == auction.seller_user_id)
                )
                if seller and deal.seller_topic_id:
                    try:
                        await bot.send_message(
                            chat_id=seller.tg_user_id,
                            message_thread_id=deal.seller_topic_id,
                            text=close_text,
                        )
                    except Exception:
                        pass

                if deal.winner_topic_id and auction.winner_user_id:
                    winner = await session.scalar(
                        select(User).where(User.id == auction.winner_user_id)
                    )
                    if winner:
                        try:
                            await bot.send_message(
                                chat_id=winner.tg_user_id,
                                message_thread_id=deal.winner_topic_id,
                                text=close_text,
                            )
                        except Exception:
                            pass

    await callback.answer("Сделка закрыта")


@router.callback_query(F.data == "deal:closecancel")
async def handle_deal_close_cancel(callback: CallbackQuery) -> None:
    await callback.answer("Отменено")
