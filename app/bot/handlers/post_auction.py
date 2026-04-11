from __future__ import annotations

import uuid

from aiogram import Bot, Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from app.db.models import Auction, User
from app.db.enums import AuctionStatus
from app.bot.states.post_auction import FeedbackFSM, DealGuarantorFSM
from app.db.session import SessionFactory
from app.services.deal_topic_service import (
    get_or_create_deal_topic,
    ensure_winner_topic,
)
from app.services.trade_feedback_service import submit_trade_feedback
from app.services.guarantor_service import create_guarantor_request
from app.services.notification_copy_service import format_user_mention

router = Router(name="post_auction")


def parse_deal_callback(data: str) -> tuple[str, uuid.UUID]:
    parts = data.split(":")
    return parts[1], uuid.UUID(parts[2])


@router.callback_query(F.data.startswith("deal:write:"))
async def handle_deal_write(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    action, auction_id = parse_deal_callback(callback.data)
    assert action == "write"

    async with SessionFactory() as session:
        async with session.begin():
            auction = await session.scalar(
                select(Auction).where(Auction.id == auction_id)
            )
            if auction is None:
                await callback.answer("Аукцион не найден", show_alert=True)
                return

            seller = await session.scalar(select(User).where(User.id == auction.seller_user_id))
            winner = (
                await session.scalar(select(User).where(User.id == auction.winner_user_id))
                if auction.winner_user_id
                else None
            )

            if winner is None:
                await callback.answer("Нет победителя для связи", show_alert=True)
                return

            caller_tg_id = callback.from_user.id
            is_seller = seller is not None and caller_tg_id == seller.tg_user_id

            seller_mention = format_user_mention(
                username=seller.username if seller else None,
                first_name=seller.first_name if seller else None,
                tg_user_id=seller.tg_user_id if seller else 0,
            ) if seller else "—"
            winner_mention = format_user_mention(
                username=winner.username,
                first_name=winner.first_name,
                tg_user_id=winner.tg_user_id,
            )

            deal = await get_or_create_deal_topic(
                session,
                bot=bot,
                auction_id=auction_id,
                seller_tg_id=seller.tg_user_id if seller else 0,
                winner_tg_id=winner.tg_user_id,
                description=auction.description,
                final_price=auction.start_price,
                seller_mention=seller_mention,
                winner_mention=winner_mention,
            )

            if is_seller:
                await ensure_winner_topic(
                    session,
                    bot=bot,
                    deal=deal,
                    winner_tg_id=winner.tg_user_id,
                    auction_id=auction_id,
                    description=auction.description,
                    final_price=auction.start_price,
                    seller_mention=seller_mention,
                    winner_mention=winner_mention,
                )

    await callback.answer("Топик сделки открыт")


@router.callback_query(F.data.startswith("deal:feedback:"))
async def handle_deal_feedback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    _, auction_id = parse_deal_callback(callback.data)

    await state.update_data(feedback_auction_id=str(auction_id))
    await state.set_state(FeedbackFSM.waiting_rating)

    rating_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐", callback_data="rate:1"),
                InlineKeyboardButton(text="⭐⭐", callback_data="rate:2"),
                InlineKeyboardButton(text="⭐⭐⭐", callback_data="rate:3"),
            ],
            [
                InlineKeyboardButton(text="⭐⭐⭐⭐", callback_data="rate:4"),
                InlineKeyboardButton(text="⭐⭐⭐⭐⭐", callback_data="rate:5"),
            ],
        ]
    )
    await callback.message.answer("⭐ Оцените сделку:", reply_markup=rating_kb)
    await callback.answer()


@router.callback_query(F.data.startswith("rate:"))
async def handle_rating(callback: CallbackQuery, state: FSMContext) -> None:
    rating = int(callback.data.split(":")[1])
    await state.update_data(feedback_rating=rating)
    await state.set_state(FeedbackFSM.waiting_comment)

    await callback.message.answer(
        "Комментарий (необязательно):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Пропустить", callback_data="rate:skip")]]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "rate:skip", FeedbackFSM.waiting_comment)
async def handle_rating_skip(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    auction_id = uuid.UUID(data["feedback_auction_id"])
    rating = data["feedback_rating"]

    async with SessionFactory() as session:
        async with session.begin():
            user = await session.scalar(
                select(User).where(User.tg_user_id == callback.from_user.id)
            )
            if user is None:
                await callback.answer("Пользователь не найден", show_alert=True)
                return
            result = await submit_trade_feedback(
                session,
                auction_id=auction_id,
                author_user_id=user.id,
                rating=rating,
                comment=None,
            )

    await state.clear()
    await callback.message.answer(result.message)
    await callback.answer()


@router.message(FeedbackFSM.waiting_comment, F.text)
async def handle_feedback_comment(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    auction_id = uuid.UUID(data["feedback_auction_id"])
    rating = data["feedback_rating"]

    async with SessionFactory() as session:
        async with session.begin():
            user = await session.scalar(
                select(User).where(User.tg_user_id == message.from_user.id)
            )
            if user is None:
                await message.answer("Пользователь не найден")
                return
            result = await submit_trade_feedback(
                session,
                auction_id=auction_id,
                author_user_id=user.id,
                rating=rating,
                comment=message.text,
            )

    await state.clear()
    await message.answer(result.message)


@router.callback_query(F.data.startswith("deal:guarant:"))
async def handle_deal_guarant(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    _, auction_id = parse_deal_callback(callback.data)

    await state.update_data(guarant_auction_id=str(auction_id))
    await state.set_state(DealGuarantorFSM.waiting_details)

    await callback.message.answer("Опишите ваш запрос гаранта:")
    await callback.answer()


@router.message(DealGuarantorFSM.waiting_details, F.text)
async def handle_guarant_details(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    auction_id = uuid.UUID(data["guarant_auction_id"])

    async with SessionFactory() as session:
        async with session.begin():
            user = await session.scalar(
                select(User).where(User.tg_user_id == message.from_user.id)
            )
            if user is None:
                await message.answer("Пользователь не найден")
                return
            await create_guarantor_request(
                session,
                submitter_user_id=user.id,
                details=message.text,
                auction_id=auction_id,
            )

    await state.clear()
    await message.answer("Запрос гаранта отправлен модераторам.")


@router.callback_query(F.data.startswith("deal:republish:"))
async def handle_deal_republish(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    _, auction_id = parse_deal_callback(callback.data)

    async with SessionFactory() as session:
        async with session.begin():
            auction = await session.scalar(
                select(Auction).where(Auction.id == auction_id).with_for_update()
            )
            if auction is None:
                await callback.answer("Аукцион не найден", show_alert=True)
                return
            if auction.status not in {AuctionStatus.ENDED, AuctionStatus.BOUGHT_OUT}:
                await callback.answer("Можно переопубликовать только завершённый лот", show_alert=True)
                return

            from app.db.models import Auction as AuctionModel

            new_auction = AuctionModel(
                seller_user_id=auction.seller_user_id,
                description=auction.description,
                photo_file_id=auction.photo_file_id,
                start_price=auction.start_price,
                buyout_price=auction.buyout_price,
                min_step=auction.min_step,
                duration_hours=auction.duration_hours,
                anti_sniper_enabled=auction.anti_sniper_enabled,
            )
            session.add(new_auction)

    await callback.message.answer("Лот скопирован как черновик. Опубликуйте его через /sell.")
    await callback.answer()
