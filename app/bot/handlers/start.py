from __future__ import annotations

import html
from datetime import UTC, datetime
import uuid

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.auction import (
    my_auction_detail_keyboard,
    my_auction_subview_keyboard,
    my_auctions_list_keyboard,
    start_private_keyboard,
)
from app.config import settings
from app.db.enums import AuctionStatus
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
from app.services.seller_dashboard_service import (
    SellerAuctionListItem,
    SellerAuctionPostItem,
    SellerBidLogItem,
    is_valid_my_auctions_filter,
    list_seller_auction_bid_logs,
    list_seller_auction_posts,
    list_seller_auctions,
    load_seller_auction,
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


MY_AUCTIONS_PAGE_SIZE = 5
MY_AUCTIONS_FILTER_LABELS: dict[str, str] = {
    "a": "Активные",
    "f": "Завершенные",
    "d": "Черновики",
    "l": "Все",
}


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


def _parse_non_negative_int(raw: str) -> int | None:
    if not raw.isdigit():
        return None
    value = int(raw)
    if value < 0 or value > 10_000:
        return None
    return value


def _parse_my_auctions_list_payload(data: str) -> tuple[str, int] | None:
    parts = data.split(":")
    if len(parts) != 5:
        return None
    _, scope, action, filter_key, page_raw = parts
    if scope != "my" or action != "list":
        return None
    if not is_valid_my_auctions_filter(filter_key):
        return None
    page = _parse_non_negative_int(page_raw)
    if page is None:
        return None
    return filter_key, page


def _parse_my_auctions_item_payload(data: str, *, action: str) -> tuple[uuid.UUID, str, int] | None:
    parts = data.split(":")
    if len(parts) != 6:
        return None
    _, scope, payload_action, auction_raw, filter_key, page_raw = parts
    if scope != "my" or payload_action != action:
        return None
    if not is_valid_my_auctions_filter(filter_key):
        return None
    page = _parse_non_negative_int(page_raw)
    if page is None:
        return None
    try:
        auction_id = uuid.UUID(auction_raw)
    except ValueError:
        return None
    return auction_id, filter_key, page


def _format_time_left(ends_at: datetime | None) -> str:
    if ends_at is None:
        return "-"

    now = datetime.now(UTC)
    if ends_at <= now:
        return "завершается"

    delta = ends_at - now
    total_seconds = int(delta.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}ч {minutes}м"
    return f"{minutes}м"


def _render_my_auctions_list_text(
    *,
    items: list[SellerAuctionListItem],
    filter_key: str,
    page: int,
    total_items: int,
) -> str:
    filter_label = MY_AUCTIONS_FILTER_LABELS.get(filter_key, "Все")
    header = f"<b>Мои аукционы</b> · {filter_label}"
    if not items:
        return (
            f"{header}\n\n"
            "У вас пока нет лотов в этом фильтре.\n"
            "Нажмите «Создать аукцион», чтобы добавить новый лот."
        )

    lines = [header, f"Всего: {total_items} · Страница: {page + 1}", ""]
    for index, item in enumerate(items, start=1 + page * MY_AUCTIONS_PAGE_SIZE):
        lines.append(
            "{} ) <code>#{}</code> · {} · <b>${}</b> · ставок: {} · осталось: {}".format(
                index,
                str(item.auction_id)[:8],
                _auction_status_label(item.status),
                item.current_price,
                item.bid_count,
                _format_time_left(item.ends_at),
            )
        )
    lines.append("")
    lines.append("Откройте лот кнопкой ниже: доступны «Ставки», «Посты» и «Фото». ")
    return "\n".join(lines)


def _auction_list_button_label(item: SellerAuctionListItem) -> str:
    return f"#{str(item.auction_id)[:8]} · {_auction_status_label(item.status)} · ${item.current_price}"


def _render_my_auction_detail_text(item: SellerAuctionListItem) -> str:
    ends_at_text = item.ends_at.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC") if item.ends_at else "-"
    return "\n".join(
        [
            f"<b>Лот #{str(item.auction_id)[:8]}</b>",
            f"Статус: <b>{_auction_status_label(item.status)}</b>",
            f"Текущая цена: <b>${item.current_price}</b>",
            f"Стартовая цена: ${item.start_price}",
            f"Кол-во ставок: {item.bid_count}",
            f"Окончание: {ends_at_text}",
            "",
            "Используйте кнопки ниже для просмотра ставок и публикаций.",
        ]
    )


def _render_bid_logs_text(*, auction_id: uuid.UUID, rows: list[SellerBidLogItem]) -> str:
    if not rows:
        return f"Ставки по лоту #{str(auction_id)[:8]} пока отсутствуют."

    lines = [f"<b>Ставки по лоту #{str(auction_id)[:8]}</b>", ""]
    for row in rows:
        actor = f"@{row.username}" if row.username else str(row.tg_user_id)
        removed_marker = " (снята)" if row.is_removed else ""
        lines.append(
            f"- {row.created_at.astimezone(UTC).strftime('%d.%m %H:%M')} · ${row.amount} · {html.escape(actor)}{removed_marker}"
        )
    return "\n".join(lines)


def _internal_chat_link_id(chat_id: int) -> str | None:
    raw = str(abs(chat_id))
    if not raw.startswith("100"):
        return None
    suffix = raw[3:]
    return suffix if suffix else None


def _resolve_post_link(chat_id: int | None, message_id: int | None, username: str | None) -> str | None:
    if chat_id is None or message_id is None:
        return None
    normalized_username = (username or "").strip().lstrip("@")
    if normalized_username:
        return f"https://t.me/{normalized_username}/{message_id}"
    internal_id = _internal_chat_link_id(chat_id)
    if internal_id is None:
        return None
    return f"https://t.me/c/{internal_id}/{message_id}"


async def _chat_username_by_id(bot: Bot, chat_id: int, cache: dict[int, str | None]) -> str | None:
    if chat_id in cache:
        return cache[chat_id]

    username: str | None = None
    try:
        chat = await bot.get_chat(chat_id)
        raw_username = getattr(chat, "username", None)
        if isinstance(raw_username, str) and raw_username.strip():
            username = raw_username.strip()
    except TelegramAPIError:
        username = None

    cache[chat_id] = username
    return username


async def _render_posts_text_and_first_link(
    *,
    bot: Bot,
    auction_id: uuid.UUID,
    rows: list[SellerAuctionPostItem],
) -> tuple[str, str | None]:
    if not rows:
        return f"Публикаций по лоту #{str(auction_id)[:8]} пока нет.", None

    lines = [f"<b>Публикации лота #{str(auction_id)[:8]}</b>", ""]
    first_link: str | None = None
    username_cache: dict[int, str | None] = {}

    for index, row in enumerate(rows, start=1):
        if row.inline_message_id:
            lines.append(f"{index}) inline-публикация (прямая ссылка недоступна)")
            continue
        if row.chat_id is None or row.message_id is None:
            lines.append(f"{index}) нет данных о публикации")
            continue

        username = await _chat_username_by_id(bot, row.chat_id, username_cache)
        post_link = _resolve_post_link(row.chat_id, row.message_id, username)
        if post_link is None:
            lines.append(f"{index}) chat_id={row.chat_id}, message_id={row.message_id}")
            continue

        if first_link is None:
            first_link = post_link
        lines.append(
            f"{index}) <a href=\"{html.escape(post_link, quote=True)}\">Открыть пост</a>"
        )

    return "\n".join(lines), first_link


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


async def _show_my_auctions_list(
    callback: CallbackQuery,
    *,
    filter_key: str,
    page: int,
    edit_message: bool,
) -> None:
    if callback.from_user is None:
        return
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("Не удалось открыть список", show_alert=True)
        return

    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, callback.from_user, mark_private_started=True)
            items, total_items = await list_seller_auctions(
                session,
                seller_user_id=user.id,
                filter_key=filter_key,
                page=page,
                page_size=MY_AUCTIONS_PAGE_SIZE,
            )

    has_prev = page > 0
    has_next = total_items > (page + 1) * MY_AUCTIONS_PAGE_SIZE
    text = _render_my_auctions_list_text(
        items=items,
        filter_key=filter_key,
        page=page,
        total_items=total_items,
    )
    keyboard = my_auctions_list_keyboard(
        auctions=[(str(item.auction_id), _auction_list_button_label(item)) for item in items],
        current_filter=filter_key,
        page=page,
        has_prev=has_prev,
        has_next=has_next,
    )

    await callback.answer()
    if edit_message:
        try:
            await callback.message.edit_text(
                text,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            return
        except TelegramBadRequest:
            pass

    await callback.message.answer(
        text,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


async def _show_my_auction_details(
    callback: CallbackQuery,
    *,
    auction_id: uuid.UUID,
    filter_key: str,
    page: int,
    edit_message: bool,
) -> None:
    if callback.from_user is None:
        return
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("Не удалось открыть лот", show_alert=True)
        return

    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, callback.from_user, mark_private_started=True)
            item = await load_seller_auction(session, seller_user_id=user.id, auction_id=auction_id)
            posts = await list_seller_auction_posts(session, seller_user_id=user.id, auction_id=auction_id)

    if item is None:
        await callback.answer("Лот не найден", show_alert=True)
        return

    first_post_url: str | None = None
    for post in posts:
        first_post_url = _resolve_post_link(post.chat_id, post.message_id, username=None)
        if first_post_url is not None:
            break

    text = _render_my_auction_detail_text(item)
    keyboard = my_auction_detail_keyboard(
        auction_id=str(item.auction_id),
        filter_key=filter_key,
        page=page,
        first_post_url=first_post_url,
    )

    await callback.answer()
    if edit_message:
        try:
            await callback.message.edit_text(
                text,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            return
        except TelegramBadRequest:
            pass

    await callback.message.answer(
        text,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "dash:my_auctions")
async def callback_my_auctions(callback: CallbackQuery) -> None:
    await _show_my_auctions_list(callback, filter_key="a", page=0, edit_message=False)


@router.callback_query(F.data.startswith("dash:my:list:"))
async def callback_my_auctions_list(callback: CallbackQuery) -> None:
    if callback.data is None:
        return
    payload = _parse_my_auctions_list_payload(callback.data)
    if payload is None:
        await callback.answer("Некорректная навигация", show_alert=True)
        return

    filter_key, page = payload
    await _show_my_auctions_list(callback, filter_key=filter_key, page=page, edit_message=True)


@router.callback_query(F.data.startswith("dash:my:view:"))
async def callback_my_auction_details(callback: CallbackQuery) -> None:
    if callback.data is None:
        return
    payload = _parse_my_auctions_item_payload(callback.data, action="view")
    if payload is None:
        await callback.answer("Некорректный лот", show_alert=True)
        return

    auction_id, filter_key, page = payload
    await _show_my_auction_details(
        callback,
        auction_id=auction_id,
        filter_key=filter_key,
        page=page,
        edit_message=True,
    )


@router.callback_query(F.data.startswith("dash:my:bids:"))
async def callback_my_auction_bids(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None:
        return
    payload = _parse_my_auctions_item_payload(callback.data, action="bids")
    if payload is None:
        await callback.answer("Некорректный лот", show_alert=True)
        return
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("Не удалось открыть ставки", show_alert=True)
        return

    auction_id, filter_key, page = payload
    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, callback.from_user, mark_private_started=True)
            item = await load_seller_auction(session, seller_user_id=user.id, auction_id=auction_id)
            bid_rows = await list_seller_auction_bid_logs(
                session,
                seller_user_id=user.id,
                auction_id=auction_id,
                limit=15,
            )

    if item is None:
        await callback.answer("Лот не найден", show_alert=True)
        return

    await callback.answer()
    await callback.message.edit_text(
        _render_bid_logs_text(auction_id=item.auction_id, rows=bid_rows),
        reply_markup=my_auction_subview_keyboard(
            auction_id=str(item.auction_id),
            filter_key=filter_key,
            page=page,
        ),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("dash:my:posts:"))
async def callback_my_auction_posts(callback: CallbackQuery, bot: Bot) -> None:
    if callback.from_user is None or callback.data is None:
        return
    payload = _parse_my_auctions_item_payload(callback.data, action="posts")
    if payload is None:
        await callback.answer("Некорректный лот", show_alert=True)
        return
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("Не удалось открыть публикации", show_alert=True)
        return

    auction_id, filter_key, page = payload
    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, callback.from_user, mark_private_started=True)
            item = await load_seller_auction(session, seller_user_id=user.id, auction_id=auction_id)
            post_rows = await list_seller_auction_posts(
                session,
                seller_user_id=user.id,
                auction_id=auction_id,
            )

    if item is None:
        await callback.answer("Лот не найден", show_alert=True)
        return

    posts_text, _ = await _render_posts_text_and_first_link(
        bot=bot,
        auction_id=item.auction_id,
        rows=post_rows,
    )

    await callback.answer()
    await callback.message.edit_text(
        posts_text,
        reply_markup=my_auction_subview_keyboard(
            auction_id=str(item.auction_id),
            filter_key=filter_key,
            page=page,
        ),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "dash:settings")
async def callback_dashboard_settings(callback: CallbackQuery) -> None:
    await callback.answer("Раздел «Настройки» в разработке.", show_alert=True)


@router.callback_query(F.data == "dash:balance")
async def callback_dashboard_balance(callback: CallbackQuery) -> None:
    await callback.answer("Раздел «Баланс» в разработке.", show_alert=True)
