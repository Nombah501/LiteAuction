from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup
from aiogram.filters import Command
from sqlalchemy import select, func, desc

from app.db.models import Bid, Auction, User
from app.db.enums import AuctionStatus
from app.db.session import SessionFactory
from app.bot.keyboards.auction import styled_button

router = Router(name="my_bids")

PAGE_SIZE = 5


def parse_mybids_page_payload(data: str) -> int:
    parts = data.split(":")
    if len(parts) != 3:
        return 0
    try:
        return int(parts[2])
    except ValueError:
        return 0


def format_time_left(ends_at: datetime) -> str:
    now = datetime.now(timezone.utc)
    delta = ends_at - now
    if delta.total_seconds() <= 0:
        return "скоро"
    total_seconds = int(delta.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours > 0:
        return f"{hours}ч {minutes}м"
    return f"{minutes}м"


def _mybids_list_keyboard(
    *,
    page: int,
    has_prev: bool,
    has_next: bool,
) -> InlineKeyboardMarkup:
    nav_row: list = []
    if has_prev:
        nav_row.append(styled_button(text="<-", callback_data=f"mybids:page:{page - 1}"))
    nav_row.append(styled_button(text=f"Стр. {page + 1}", callback_data=f"mybids:page:{page}"))
    if has_next:
        nav_row.append(styled_button(text="->", callback_data=f"mybids:page:{page + 1}"))
    return InlineKeyboardMarkup(inline_keyboard=[nav_row])


@router.message(Command("mybids"))
async def handle_mybids_command(message: Message) -> None:
    if message.from_user is None:
        return

    async with SessionFactory() as session:
        user = await session.scalar(
            select(User).where(User.tg_user_id == message.from_user.id)
        )
        if user is None:
            await message.answer("Сначала откройте бота через /start")
            return

        text = await _build_mybids_text(session, user.id, page=0)
        kb = await _build_mybids_keyboard(session, user.id, page=0)

    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("mybids:page:"))
async def handle_mybids_page(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        return

    page = parse_mybids_page_payload(callback.data)

    async with SessionFactory() as session:
        user = await session.scalar(
            select(User).where(User.tg_user_id == callback.from_user.id)
        )
        if user is None:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        text = await _build_mybids_text(session, user.id, page=page)
        kb = await _build_mybids_keyboard(session, user.id, page=page)

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "dash:my_bids")
async def handle_mybids_dashboard_callback(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        return

    async with SessionFactory() as session:
        user = await session.scalar(
            select(User).where(User.tg_user_id == callback.from_user.id)
        )
        if user is None:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        text = await _build_mybids_text(session, user.id, page=0)
        kb = await _build_mybids_keyboard(session, user.id, page=0)

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


async def _build_mybids_text(session, user_id: int, page: int) -> str:
    active_statuses = {AuctionStatus.ACTIVE, AuctionStatus.FROZEN}
    finished_statuses = {AuctionStatus.ENDED, AuctionStatus.BOUGHT_OUT}

    latest_bid_subq = (
        select(Bid.auction_id, func.max(Bid.created_at).label("last_bid_at"))
        .where(Bid.user_id == user_id, Bid.is_removed.is_(False))
        .group_by(Bid.auction_id)
        .subquery()
    )

    active_query = (
        select(Auction, latest_bid_subq.c.last_bid_at)
        .join(latest_bid_subq, Auction.id == latest_bid_subq.c.auction_id)
        .where(Auction.status.in_(active_statuses))
        .order_by(Auction.ends_at.asc())
    )
    active_result = (await session.execute(active_query)).all()

    finished_query = (
        select(Auction, latest_bid_subq.c.last_bid_at)
        .join(latest_bid_subq, Auction.id == latest_bid_subq.c.auction_id)
        .where(Auction.status.in_(finished_statuses))
        .order_by(desc(latest_bid_subq.c.last_bid_at))
    )
    finished_result = (await session.execute(finished_query)).all()

    lines: list[str] = []

    if active_result:
        lines.append(f"🔥 <b>Активные ставки ({len(active_result)})</b>\n")
        for auction, _last_bid_at in active_result:
            short_id = str(auction.id)[:8]
            desc = auction.description[:40] + ("..." if len(auction.description) > 40 else "")

            top_bid = await session.scalar(
                select(func.max(Bid.amount))
                .where(Bid.auction_id == auction.id, Bid.is_removed.is_(False))
            )
            my_max = await session.scalar(
                select(func.max(Bid.amount))
                .where(Bid.auction_id == auction.id, Bid.user_id == user_id, Bid.is_removed.is_(False))
            )

            is_top = my_max is not None and my_max >= (top_bid or 0)
            status_icon = "✅ ТОП" if is_top else "⚠️ Перебили"

            time_str = format_time_left(auction.ends_at) if auction.ends_at else "—"

            lines.append(f"Лот #{short_id} • {desc}")
            lines.append(f"💰 Ваша ставка: ${my_max} ({status_icon}) | До финиша: {time_str}")
            lines.append("")

    if finished_result:
        lines.append(f"✅ <b>Завершённые ({len(finished_result)})</b>")
        for auction, _last_bid_at in finished_result[:5]:
            short_id = str(auction.id)[:8]
            desc = auction.description[:40] + ("..." if len(auction.description) > 40 else "")

            won = auction.winner_user_id == user_id
            result_text = "ВЫИГРАЛИ" if won else "Проиграли"

            lines.append(f"Лот #{short_id} • {desc} — {result_text}")

    if not active_result and not finished_result:
        lines.append("У вас пока нет ставок.")
        lines.append("Ищите лоты в подключённых чатах и каналах!")

    return "\n".join(lines)


async def _build_mybids_keyboard(session, user_id: int, page: int) -> InlineKeyboardMarkup:
    total = await session.scalar(
        select(func.count(func.distinct(Bid.auction_id)))
        .where(Bid.user_id == user_id, Bid.is_removed.is_(False))
    )
    total = total or 0
    has_prev = page > 0
    has_next = (page + 1) * PAGE_SIZE < total

    if not has_prev and not has_next:
        return InlineKeyboardMarkup(inline_keyboard=[])

    return _mybids_list_keyboard(page=page, has_prev=has_prev, has_next=has_next)
