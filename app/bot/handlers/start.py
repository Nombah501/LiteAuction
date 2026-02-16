from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from app.bot.keyboards.auction import start_private_keyboard
from app.config import settings
from app.db.enums import AuctionStatus
from app.db.models import Auction, Bid
from app.db.session import SessionFactory
from app.services.appeal_service import create_appeal_from_ref, redeem_appeal_priority_boost
from app.services.moderation_service import has_moderator_access, is_moderator_tg_user
from app.services.moderation_topic_router import ModerationTopicSection, send_section_message
from app.services.private_topics_service import (
    PrivateTopicPurpose,
    enforce_message_topic,
    render_user_topics_overview,
    resolve_user_topic_thread_id,
    send_user_topic_message,
)
from app.services.user_service import upsert_user

router = Router(name="start")


def _extract_start_payload(message: Message) -> str | None:
    text = (message.text or "").strip()
    if not text:
        return None
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    payload = parts[1].strip()
    return payload or None


def _appeal_acceptance_text(appeal_id: int) -> str:
    return (
        f"Апелляция #{appeal_id} принята. "
        "Мы передали запрос модераторам и вернемся с ответом."
    )


def _extract_boost_appeal_id(text: str | None) -> int | None:
    raw = (text or "").strip()
    parts = raw.split(maxsplit=1)
    if len(parts) != 2:
        return None
    candidate = parts[1].strip()
    if not candidate.isdigit():
        return None
    return int(candidate)


def _auction_status_label(status: AuctionStatus) -> str:
    labels = {
        AuctionStatus.DRAFT: "Черновик",
        AuctionStatus.ACTIVE: "Активен",
        AuctionStatus.ENDED: "Завершен",
        AuctionStatus.BOUGHT_OUT: "Выкуплен",
        AuctionStatus.CANCELLED: "Отменен",
        AuctionStatus.FROZEN: "Заморожен",
    }
    return labels[status]


def _render_my_auctions_text(
    rows: list[tuple[object, AuctionStatus, int, int | None]],
) -> str:
    if not rows:
        return "У вас пока нет аукционов. Нажмите «Создать аукцион», чтобы добавить первый лот."

    lines = ["Мои аукционы (последние 10):", ""]
    for idx, (auction_id, status, start_price, top_bid_amount) in enumerate(rows, start=1):
        current_price = top_bid_amount if top_bid_amount is not None else start_price
        lines.append(
            f"{idx}) #{str(auction_id)[:8]} | {_auction_status_label(status)} | текущая цена: ${current_price}"
        )
    return "\n".join(lines)


async def _load_my_auctions_rows(
    *,
    session,
    seller_user_id: int,
    limit: int = 10,
) -> list[tuple[object, AuctionStatus, int, int | None]]:
    top_bid_amount = (
        select(func.max(Bid.amount))
        .where(Bid.auction_id == Auction.id, Bid.is_removed.is_(False))
        .correlate(Auction)
        .scalar_subquery()
    )
    stmt = (
        select(
            Auction.id,
            Auction.status,
            Auction.start_price,
            top_bid_amount.label("top_bid_amount"),
        )
        .where(Auction.seller_user_id == seller_user_id)
        .order_by(Auction.created_at.desc())
        .limit(limit)
    )
    return [
        (auction_id, status, start_price, top_bid)
        for auction_id, status, start_price, top_bid in (await session.execute(stmt)).all()
    ]


async def _can_show_moderation_button(*, session, tg_user_id: int) -> bool:
    if is_moderator_tg_user(tg_user_id):
        return True
    return await has_moderator_access(session, tg_user_id)


async def _notify_moderators_about_appeal(
    bot: Bot,
    message: Message,
    appeal_ref: str,
    *,
    appeal_id: int,
) -> None:
    if message.from_user is None:
        return

    username = f"@{message.from_user.username}" if message.from_user.username else "-"
    text = (
        "Новая апелляция\n"
        f"ID апелляции: {appeal_id}\n"
        f"Референс: {appeal_ref}\n"
        f"TG user id: {message.from_user.id}\n"
        f"Юзернейм: {username}"
    )

    await send_section_message(bot, section=ModerationTopicSection.APPEALS, text=text)


async def _notify_moderators_about_appeal_boost(
    bot: Bot,
    message: Message,
    *,
    appeal_id: int,
) -> None:
    if message.from_user is None:
        return

    username = f"@{message.from_user.username}" if message.from_user.username else "-"
    text = (
        "⚡ Буст апелляции\n"
        f"ID апелляции: {appeal_id}\n"
        f"TG user id: {message.from_user.id}\n"
        f"Юзернейм: {username}"
    )
    await send_section_message(bot, section=ModerationTopicSection.APPEALS, text=text)


@router.message(Command("boostappeal"), F.chat.type == ChatType.PRIVATE)
async def command_boost_appeal(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return

    appeal_id = _extract_boost_appeal_id(message.text)
    if appeal_id is None:
        await message.answer("Формат: /boostappeal <appeal_id>")
        return

    result_message = ""
    result_changed = False
    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, message.from_user, mark_private_started=True)
            if not await enforce_message_topic(
                message,
                bot=bot,
                session=session,
                user=user,
                purpose=PrivateTopicPurpose.POINTS,
                command_hint=f"/boostappeal {appeal_id}",
            ):
                return
            result = await redeem_appeal_priority_boost(
                session,
                appeal_id=appeal_id,
                appellant_user_id=user.id,
            )
            if not result.ok:
                await message.answer(result.message)
                return

            result_message = result.message
            result_changed = result.changed

    if result_changed:
        await _notify_moderators_about_appeal_boost(bot, message, appeal_id=appeal_id)
        await message.answer(f"{result_message}. Модераторы получили уведомление.")
        return

    await message.answer(result_message)


@router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def handle_start_private(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return

    payload = _extract_start_payload(message)
    appeal_id: int | None = None
    topics_overview: str | None = None
    auctions_thread_id: int | None = None
    show_moderation_button = False

    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, message.from_user, mark_private_started=True)
            show_moderation_button = await _can_show_moderation_button(
                session=session,
                tg_user_id=message.from_user.id,
            )
            if settings.private_topics_enabled and settings.private_topics_autocreate_on_start:
                topics_overview = await render_user_topics_overview(
                    session,
                    bot,
                    user=user,
                    telegram_user=message.from_user,
                )
                auctions_thread_id = await resolve_user_topic_thread_id(
                    session,
                    bot,
                    user=user,
                    purpose=PrivateTopicPurpose.AUCTIONS,
                    telegram_user=message.from_user,
                )
            if payload is not None and payload.startswith("appeal_"):
                appeal_ref = payload[len("appeal_") :] or "manual"
                appeal = await create_appeal_from_ref(
                    session,
                    appellant_user_id=user.id,
                    appeal_ref=appeal_ref,
                )
                appeal_id = appeal.id

    dashboard_keyboard = start_private_keyboard(show_moderation_button=show_moderation_button)

    if payload is not None and payload.startswith("appeal_") and appeal_id is not None:
        appeal_ref = payload[len("appeal_") :] or "manual"
        await _notify_moderators_about_appeal(
            bot,
            message,
            appeal_ref,
            appeal_id=appeal_id,
        )
        await message.answer(
            _appeal_acceptance_text(appeal_id),
            reply_markup=dashboard_keyboard,
        )
        return

    start_text = (
        "Привет! Я LiteAuction bot.\n"
        "Создавайте аукционы через кнопку ниже.\n"
        "Для модераторов там же есть вход в панель.\n\n"
        "В посте будут live-ставки, топ-3, анти-снайпер и выкуп."
    )
    sent_to_auctions = False
    if settings.private_topics_enabled:
        sent_to_auctions = await send_user_topic_message(
            bot,
            tg_user_id=message.from_user.id,
            purpose=PrivateTopicPurpose.AUCTIONS,
            text=start_text,
            reply_markup=dashboard_keyboard,
        )

    if not sent_to_auctions:
        await message.answer(start_text, reply_markup=dashboard_keyboard)
    elif (
        auctions_thread_id is not None
        and getattr(message, "message_thread_id", None) != auctions_thread_id
    ):
        await message.answer("Открыл раздел «Аукционы». Продолжайте там.")

    if topics_overview is not None and (
        "недоступны" in topics_overview.lower() or "ограничено" in topics_overview.lower()
    ):
        await message.answer(topics_overview)


@router.message(Command("topics"), F.chat.type == ChatType.PRIVATE)
async def command_topics(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return

    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, message.from_user, mark_private_started=True)
            overview = await render_user_topics_overview(
                session,
                bot,
                user=user,
                telegram_user=message.from_user,
            )

    await message.answer(overview)


@router.message(CommandStart())
async def handle_start_non_private(message: Message) -> None:
    await message.answer("Для настройки и уведомлений откройте бота в личных сообщениях.")


@router.callback_query(F.data == "dash:my_auctions")
async def callback_my_auctions(callback: CallbackQuery) -> None:
    if callback.from_user is None:
        return

    rows: list[tuple[object, AuctionStatus, int, int | None]] = []
    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, callback.from_user, mark_private_started=True)
            rows = await _load_my_auctions_rows(session=session, seller_user_id=user.id, limit=10)

    await callback.answer()
    if callback.message is not None and isinstance(callback.message, Message):
        await callback.message.answer(_render_my_auctions_text(rows))


@router.callback_query(F.data == "dash:settings")
async def callback_dashboard_settings(callback: CallbackQuery) -> None:
    await callback.answer("Раздел «Настройки» в разработке.", show_alert=True)


@router.callback_query(F.data == "dash:balance")
async def callback_dashboard_balance(callback: CallbackQuery) -> None:
    await callback.answer("Раздел «Баланс» в разработке.", show_alert=True)
