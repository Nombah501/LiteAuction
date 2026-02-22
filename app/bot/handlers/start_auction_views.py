from __future__ import annotations

import html
from datetime import UTC, datetime
import uuid

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.db.enums import AuctionStatus
from app.services.seller_dashboard_service import (
    SellerAuctionListItem,
    SellerAuctionPostItem,
    SellerBidLogItem,
    is_valid_my_auctions_filter,
    is_valid_my_auctions_sort,
)

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
