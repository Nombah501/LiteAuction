from __future__ import annotations

import html
from datetime import UTC, datetime
import uuid

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.bot.keyboards.auction import (
    auction_report_gateway_keyboard,
    my_auction_detail_keyboard,
    my_auction_subview_keyboard,
    my_auctions_list_keyboard,
    notification_onboarding_keyboard,
    notification_settings_keyboard,
    start_private_keyboard,
)
from app.config import settings
from app.db.enums import AuctionStatus
from app.db.session import SessionFactory
from app.services.appeal_service import create_appeal_from_ref, redeem_appeal_priority_boost
from app.services.auction_service import load_auction_view, refresh_auction_posts
from app.services.moderation_service import has_moderator_access, is_moderator_tg_user
from app.services.moderation_topic_router import ModerationTopicSection, send_section_message
from app.services.private_topics_service import (
    PrivateTopicPurpose,
    enforce_message_topic,
    render_user_topics_overview,
    resolve_user_topic_thread_id,
    send_user_topic_message,
)
from app.services.notification_policy_service import (
    NotificationEventType,
    NotificationPreset,
    NotificationSettingsSnapshot,
    load_notification_settings,
    notification_event_from_action_key,
    set_notification_event_enabled,
    set_master_notifications_enabled,
    set_notification_preset,
    toggle_notification_event,
)
from app.services.seller_dashboard_service import (
    SellerAuctionListItem,
    SellerAuctionPostItem,
    SellerBidLogItem,
    is_valid_my_auctions_filter,
    is_valid_my_auctions_sort,
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


def _extract_report_auction_id(payload: str | None) -> uuid.UUID | None:
    if payload is None or not payload.startswith("report_"):
        return None
    auction_raw = payload[len("report_") :].strip()
    if not auction_raw:
        return None
    try:
        return uuid.UUID(auction_raw)
    except ValueError:
        return None


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

MY_AUCTIONS_SORT_LABELS: dict[str, str] = {
    "n": "Новые",
    "e": "Скоро финиш",
    "b": "Больше ставок",
}

_SETTINGS_TOGGLE_EVENTS: dict[str, NotificationEventType] = {
    "outbid": NotificationEventType.AUCTION_OUTBID,
    "finish": NotificationEventType.AUCTION_FINISH,
    "win": NotificationEventType.AUCTION_WIN,
    "mod": NotificationEventType.AUCTION_MOD_ACTION,
    "points": NotificationEventType.POINTS,
    "support": NotificationEventType.SUPPORT,
}


def _preset_title(preset: NotificationPreset) -> str:
    labels = {
        NotificationPreset.RECOMMENDED: "Рекомендуемые",
        NotificationPreset.IMPORTANT: "Только важные",
        NotificationPreset.ALL: "Все",
        NotificationPreset.CUSTOM: "Вручную",
    }
    return labels[preset]


def _render_settings_text(snapshot: NotificationSettingsSnapshot) -> str:
    global_state = "включены" if snapshot.master_enabled else "отключены"
    configured_state = "настроены" if snapshot.configured else "не настроены"
    return "\n".join(
        [
            "<b>Настройки уведомлений</b>",
            f"Глобально: <b>{global_state}</b>",
            f"Пресет: <b>{_preset_title(snapshot.preset)}</b>",
            f"Статус первичной настройки: <b>{configured_state}</b>",
            "",
            "Выберите пресет или переключите отдельные события кнопками ниже.",
        ]
    )


def _settings_keyboard(snapshot: NotificationSettingsSnapshot):
    return notification_settings_keyboard(
        master_enabled=snapshot.master_enabled,
        preset=snapshot.preset.value,
        outbid_enabled=snapshot.outbid_enabled,
        auction_finish_enabled=snapshot.auction_finish_enabled,
        auction_win_enabled=snapshot.auction_win_enabled,
        auction_mod_actions_enabled=snapshot.auction_mod_actions_enabled,
        points_enabled=snapshot.points_enabled,
        support_enabled=snapshot.support_enabled,
    )


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


def _parse_my_auctions_list_payload(data: str) -> tuple[str, str, int] | None:
    parts = data.split(":")
    if len(parts) == 5:
        _, scope, action, filter_key, page_raw = parts
        sort_key = "n"
    elif len(parts) == 6:
        _, scope, action, filter_key, sort_key, page_raw = parts
    else:
        return None
    if scope != "my" or action != "list":
        return None
    if not is_valid_my_auctions_filter(filter_key):
        return None
    if not is_valid_my_auctions_sort(sort_key):
        return None
    page = _parse_non_negative_int(page_raw)
    if page is None:
        return None
    return filter_key, sort_key, page


def _parse_my_auctions_item_payload(data: str, *, action: str) -> tuple[uuid.UUID, str, str, int] | None:
    parts = data.split(":")
    if len(parts) == 6:
        _, scope, payload_action, auction_raw, filter_key, page_raw = parts
        sort_key = "n"
    elif len(parts) == 7:
        _, scope, payload_action, auction_raw, filter_key, sort_key, page_raw = parts
    else:
        return None
    if scope != "my" or payload_action != action:
        return None
    if not is_valid_my_auctions_filter(filter_key):
        return None
    if not is_valid_my_auctions_sort(sort_key):
        return None
    page = _parse_non_negative_int(page_raw)
    if page is None:
        return None
    try:
        auction_id = uuid.UUID(auction_raw)
    except ValueError:
        return None
    return auction_id, filter_key, sort_key, page


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
    sort_key: str,
    page: int,
    total_items: int,
) -> str:
    filter_label = MY_AUCTIONS_FILTER_LABELS.get(filter_key, "Все")
    sort_label = MY_AUCTIONS_SORT_LABELS.get(sort_key, "Новые")
    header = f"<b>Мои аукционы</b> · {filter_label} · {sort_label}"
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
    growth_abs = item.current_price - item.start_price
    growth_percent = 0.0
    if item.start_price > 0:
        growth_percent = (growth_abs / item.start_price) * 100
    growth_sign = "+" if growth_abs >= 0 else ""

    avg_growth_per_bid = 0.0
    if item.bid_count > 0:
        avg_growth_per_bid = growth_abs / item.bid_count

    outcome_label = "Финальная цена" if item.status in {
        AuctionStatus.ENDED,
        AuctionStatus.BOUGHT_OUT,
        AuctionStatus.CANCELLED,
    } else "Текущая цена"

    return "\n".join(
        [
            f"<b>Лот #{str(item.auction_id)[:8]}</b>",
            f"Статус: <b>{_auction_status_label(item.status)}</b>",
            f"{outcome_label}: <b>${item.current_price}</b>",
            f"Стартовая цена: ${item.start_price}",
            f"Кол-во ставок: {item.bid_count}",
            f"Прирост к старту: {growth_sign}${growth_abs} ({growth_sign}{growth_percent:.1f}%)",
            f"Ср. прирост на ставку: ${avg_growth_per_bid:.2f}",
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


def _dashboard_start_text() -> str:
    return (
        "Привет! Я LiteAuction bot.\n"
        "Создавайте аукционы через кнопку ниже.\n"
        "Для модераторов там же есть вход в панель.\n\n"
        "В посте будут live-ставки, топ-3, анти-снайпер и выкуп."
    )


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
    report_auction_id = _extract_report_auction_id(payload)
    report_auction_found = False
    topics_overview: str | None = None
    auctions_thread_id: int | None = None
    show_moderation_button = False
    notification_snapshot: NotificationSettingsSnapshot | None = None

    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, message.from_user, mark_private_started=True)
            show_moderation_button = await _can_show_moderation_button(
                session=session,
                tg_user_id=message.from_user.id,
            )
            notification_snapshot = await load_notification_settings(session, user_id=user.id)
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
            if report_auction_id is not None:
                report_auction_found = (await load_auction_view(session, report_auction_id)) is not None

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

    if report_auction_id is not None:
        short_id = str(report_auction_id)[:8]
        if report_auction_found:
            report_text = (
                f"Лот #{short_id} открыт в поддержке.\n"
                "Если видите нарушение, отправьте жалобу кнопкой ниже."
            )
            report_keyboard = auction_report_gateway_keyboard(str(report_auction_id))
            sent_to_auctions = False
            if settings.private_topics_enabled:
                sent_to_auctions = await send_user_topic_message(
                    bot,
                    tg_user_id=message.from_user.id,
                    purpose=PrivateTopicPurpose.AUCTIONS,
                    text=report_text,
                    reply_markup=report_keyboard,
                )

            if not sent_to_auctions:
                await message.answer(report_text, reply_markup=report_keyboard)
            return

        await message.answer(
            f"Лот #{short_id} не найден или уже удален. Проверьте ссылку и попробуйте снова.",
            reply_markup=dashboard_keyboard,
        )
        return

    start_text = _dashboard_start_text()
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

    if notification_snapshot is not None and not notification_snapshot.configured:
        onboarding_text = (
            "Выберите профиль уведомлений.\n"
            "Рекомендуем начать с «Рекомендуемые»."
        )
        onboarding_keyboard = notification_onboarding_keyboard(preset=notification_snapshot.preset.value)
        sent_onboarding = False
        if settings.private_topics_enabled:
            sent_onboarding = await send_user_topic_message(
                bot,
                tg_user_id=message.from_user.id,
                purpose=PrivateTopicPurpose.AUCTIONS,
                text=onboarding_text,
                reply_markup=onboarding_keyboard,
            )
        if not sent_onboarding:
            await message.answer(onboarding_text, reply_markup=onboarding_keyboard)


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


@router.message(Command("settings"), F.chat.type == ChatType.PRIVATE)
async def command_settings(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return

    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, message.from_user, mark_private_started=True)
            snapshot = await load_notification_settings(session, user_id=user.id)

    if snapshot is None:
        await message.answer("Не удалось загрузить настройки")
        return

    text = _render_settings_text(snapshot)
    keyboard = _settings_keyboard(snapshot)
    delivered = False
    if settings.private_topics_enabled:
        delivered = await send_user_topic_message(
            bot,
            tg_user_id=message.from_user.id,
            purpose=PrivateTopicPurpose.AUCTIONS,
            text=text,
            reply_markup=keyboard,
        )

    if not delivered:
        await message.answer(text, reply_markup=keyboard)


@router.message(CommandStart())
async def handle_start_non_private(message: Message) -> None:
    await message.answer("Для настройки и уведомлений откройте бота в личных сообщениях.")


async def _show_dashboard_home(callback: CallbackQuery, *, edit_message: bool) -> None:
    if callback.from_user is None:
        return
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("Не удалось открыть меню", show_alert=True)
        return

    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, callback.from_user, mark_private_started=True)
            show_moderation_button = await _can_show_moderation_button(
                session=session,
                tg_user_id=user.tg_user_id,
            )

    text = _dashboard_start_text()
    keyboard = start_private_keyboard(show_moderation_button=show_moderation_button)
    if edit_message:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard, disable_web_page_preview=True)
            return
        except TelegramBadRequest:
            pass

    await callback.message.answer(text, reply_markup=keyboard, disable_web_page_preview=True)


async def _show_settings_card(
    callback: CallbackQuery,
    *,
    edit_message: bool,
    answer_callback: bool = True,
) -> None:
    if callback.from_user is None:
        return
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("Не удалось открыть настройки", show_alert=True)
        return

    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, callback.from_user, mark_private_started=True)
            snapshot = await load_notification_settings(session, user_id=user.id)

    if snapshot is None:
        await callback.answer("Настройки недоступны", show_alert=True)
        return

    text = _render_settings_text(snapshot)
    keyboard = _settings_keyboard(snapshot)
    if answer_callback:
        await callback.answer()
    if edit_message:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard, disable_web_page_preview=True)
            return
        except TelegramBadRequest:
            pass

    await callback.message.answer(text, reply_markup=keyboard, disable_web_page_preview=True)


async def _show_my_auctions_list(
    callback: CallbackQuery,
    *,
    filter_key: str,
    sort_key: str,
    page: int,
    edit_message: bool,
    answer_callback: bool = True,
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
                sort_key=sort_key,
                page=page,
                page_size=MY_AUCTIONS_PAGE_SIZE,
            )

    has_prev = page > 0
    has_next = total_items > (page + 1) * MY_AUCTIONS_PAGE_SIZE
    text = _render_my_auctions_list_text(
        items=items,
        filter_key=filter_key,
        sort_key=sort_key,
        page=page,
        total_items=total_items,
    )
    keyboard = my_auctions_list_keyboard(
        auctions=[(str(item.auction_id), _auction_list_button_label(item)) for item in items],
        current_filter=filter_key,
        current_sort=sort_key,
        page=page,
        has_prev=has_prev,
        has_next=has_next,
    )

    if answer_callback:
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
    bot: Bot,
    auction_id: uuid.UUID,
    filter_key: str,
    sort_key: str,
    page: int,
    edit_message: bool,
    answer_callback: bool = True,
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
    username_cache: dict[int, str | None] = {}
    for post in posts:
        if post.chat_id is not None:
            username = await _chat_username_by_id(bot, post.chat_id, username_cache)
        else:
            username = None
        first_post_url = _resolve_post_link(post.chat_id, post.message_id, username=username)
        if first_post_url is not None:
            break

    text = _render_my_auction_detail_text(item)
    keyboard = my_auction_detail_keyboard(
        auction_id=str(item.auction_id),
        filter_key=filter_key,
        sort_key=sort_key,
        page=page,
        status=item.status,
        first_post_url=first_post_url,
    )

    if answer_callback:
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
    await _show_my_auctions_list(callback, filter_key="a", sort_key="n", page=0, edit_message=False)


@router.callback_query(F.data.startswith("dash:my:list:"))
async def callback_my_auctions_list(callback: CallbackQuery) -> None:
    if callback.data is None:
        return
    payload = _parse_my_auctions_list_payload(callback.data)
    if payload is None:
        await callback.answer("Некорректная навигация", show_alert=True)
        return

    filter_key, sort_key, page = payload
    await _show_my_auctions_list(
        callback,
        filter_key=filter_key,
        sort_key=sort_key,
        page=page,
        edit_message=True,
    )


@router.callback_query(F.data.startswith("dash:my:view:"))
async def callback_my_auction_details(callback: CallbackQuery, bot: Bot) -> None:
    if callback.data is None:
        return
    payload = _parse_my_auctions_item_payload(callback.data, action="view")
    if payload is None:
        await callback.answer("Некорректный лот", show_alert=True)
        return

    auction_id, filter_key, sort_key, page = payload
    await _show_my_auction_details(
        callback,
        bot=bot,
        auction_id=auction_id,
        filter_key=filter_key,
        sort_key=sort_key,
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

    auction_id, filter_key, sort_key, page = payload
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
            sort_key=sort_key,
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

    auction_id, filter_key, sort_key, page = payload
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
            sort_key=sort_key,
            page=page,
        ),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("dash:my:refresh:"))
async def callback_my_auction_refresh_posts(callback: CallbackQuery, bot: Bot) -> None:
    if callback.from_user is None or callback.data is None:
        return
    if callback.message is None or not isinstance(callback.message, Message):
        await callback.answer("Не удалось обновить карточку", show_alert=True)
        return

    payload = _parse_my_auctions_item_payload(callback.data, action="refresh")
    if payload is None:
        await callback.answer("Некорректный лот", show_alert=True)
        return

    auction_id, filter_key, sort_key, page = payload
    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, callback.from_user, mark_private_started=True)
            item = await load_seller_auction(session, seller_user_id=user.id, auction_id=auction_id)

    if item is None:
        await callback.answer("Лот не найден", show_alert=True)
        return

    if item.status not in {AuctionStatus.ACTIVE, AuctionStatus.FROZEN}:
        await callback.answer("Обновление доступно только для активных лотов", show_alert=True)
        return

    await refresh_auction_posts(bot, item.auction_id)
    await callback.answer("Посты обновлены")
    await _show_my_auction_details(
        callback,
        bot=bot,
        auction_id=item.auction_id,
        filter_key=filter_key,
        sort_key=sort_key,
        page=page,
        edit_message=True,
        answer_callback=False,
    )


@router.callback_query(F.data == "dash:settings")
async def callback_dashboard_settings(callback: CallbackQuery) -> None:
    await _show_settings_card(callback, edit_message=True)


@router.callback_query(F.data == "dash:home")
async def callback_dashboard_home(callback: CallbackQuery) -> None:
    await callback.answer()
    await _show_dashboard_home(callback, edit_message=True)


@router.callback_query(F.data.startswith("dash:settings:"))
async def callback_dashboard_settings_action(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None:
        return

    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Некорректное действие", show_alert=True)
        return

    _, _, action, raw_value = parts
    result_message = "Сохранено"

    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, callback.from_user, mark_private_started=True)
            if action == "master":
                if raw_value not in {"0", "1"}:
                    await callback.answer("Некорректный переключатель", show_alert=True)
                    return
                snapshot = await set_master_notifications_enabled(
                    session,
                    user_id=user.id,
                    enabled=raw_value == "1",
                )
                result_message = "Глобальный переключатель обновлен"
            elif action == "preset":
                try:
                    preset = NotificationPreset(raw_value)
                except ValueError:
                    await callback.answer("Неизвестный пресет", show_alert=True)
                    return
                snapshot = await set_notification_preset(
                    session,
                    user_id=user.id,
                    preset=preset,
                    mark_configured=True,
                )
                result_message = f"Пресет «{_preset_title(preset)}» применен"
            elif action == "toggle":
                event_type = _SETTINGS_TOGGLE_EVENTS.get(raw_value)
                if event_type is None:
                    await callback.answer("Неизвестный тип уведомления", show_alert=True)
                    return
                snapshot = await toggle_notification_event(
                    session,
                    user_id=user.id,
                    event_type=event_type,
                )
                result_message = "Событие обновлено"
            else:
                await callback.answer("Некорректное действие", show_alert=True)
                return

    if snapshot is None:
        await callback.answer("Не удалось сохранить настройки", show_alert=True)
        return

    await callback.answer(result_message)
    await _show_settings_card(callback, edit_message=True, answer_callback=False)


@router.callback_query(F.data.startswith("notif:mute:"))
async def callback_notification_mute_type(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None:
        return

    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Некорректное действие", show_alert=True)
        return

    event_type = notification_event_from_action_key(parts[2])
    if event_type is None:
        await callback.answer("Неизвестный тип уведомления", show_alert=True)
        return

    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, callback.from_user, mark_private_started=True)
            snapshot = await set_notification_event_enabled(
                session,
                user_id=user.id,
                event_type=event_type,
                enabled=False,
                mark_configured=True,
            )

    if snapshot is None:
        await callback.answer("Не удалось обновить настройки", show_alert=True)
        return

    if callback.message is not None and isinstance(callback.message, Message):
        markup = callback.message.reply_markup
        if isinstance(markup, InlineKeyboardMarkup):
            rows = [
                row
                for row in markup.inline_keyboard
                if not any(button.callback_data == callback.data for button in row)
            ]
            try:
                await callback.message.edit_reply_markup(
                    reply_markup=None if not rows else InlineKeyboardMarkup(inline_keyboard=rows)
                )
            except TelegramBadRequest:
                pass

    await callback.answer(
        "Тип уведомления отключен. Вернуть можно в /settings.",
        show_alert=True,
    )


@router.callback_query(F.data == "dash:balance")
async def callback_dashboard_balance(callback: CallbackQuery) -> None:
    await callback.answer("Раздел «Баланс» в разработке.", show_alert=True)
