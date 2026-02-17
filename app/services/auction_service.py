from __future__ import annotations

import html
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import InlineKeyboardMarkup, InputMediaPhoto
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.auction import auction_active_keyboard, open_auction_post_keyboard
from app.config import settings
from app.db.enums import AuctionStatus
from app.db.models import Auction, AuctionPhoto, AuctionPost, Bid, BlacklistEntry, Complaint, User
from app.db.session import SessionFactory
from app.services.fraud_service import evaluate_and_store_bid_fraud_signal
from app.services.message_effects_service import (
    AuctionMessageEffectEvent,
    resolve_auction_message_effect_id,
)
from app.services.private_topics_service import PrivateTopicPurpose, send_user_topic_message
from app.services.notification_policy_service import NotificationEventType

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TopBidView:
    amount: int
    user_id: int
    tg_user_id: int
    username: str | None
    first_name: str | None
    created_at: datetime


@dataclass(slots=True)
class AuctionView:
    auction: Auction
    seller: User
    winner: User | None
    top_bids: list[TopBidView]
    current_price: int
    minimum_next_bid: int
    open_complaints: int
    photo_count: int


@dataclass(slots=True)
class BidActionResult:
    success: bool
    should_refresh: bool
    alert_text: str
    outbid_tg_user_id: int | None = None
    winner_tg_user_id: int | None = None
    seller_tg_user_id: int | None = None
    auction_finished: bool = False
    placed_bid_amount: int | None = None
    created_bid_id: uuid.UUID | None = None
    fraud_signal_id: int | None = None


@dataclass(slots=True)
class FinalizeResult:
    auction_id: uuid.UUID
    winner_tg_user_id: int | None
    seller_tg_user_id: int


def parse_auction_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def ceil_to_next_hour(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    rounded = timestamp.replace(minute=0, second=0, microsecond=0)
    if rounded == timestamp:
        return rounded
    return rounded + timedelta(hours=1)


def _get_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(settings.tz)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone(_get_timezone()).strftime("%d.%m.%Y %H:%M")


def _format_user_mention(user: User | None) -> str:
    if user is None:
        return "-"
    if user.username:
        return f"@{html.escape(user.username)}"
    display = user.first_name or "Пользователь"
    return f'<a href="tg://user?id={user.tg_user_id}">{html.escape(display)}</a>'


def _format_top_bids(top_bids: list[TopBidView]) -> str:
    medal = ["🥇", "🥈", "🥉"]
    if not top_bids:
        return "\n".join(f"{icon} —" for icon in medal)

    lines: list[str] = []
    for idx in range(3):
        if idx >= len(top_bids):
            lines.append(f"{medal[idx]} —")
            continue
        bid = top_bids[idx]
        if bid.username:
            actor = f"@{html.escape(bid.username)}"
        else:
            fallback = html.escape(bid.first_name or "Пользователь")
            actor = f'<a href="tg://user?id={bid.tg_user_id}">{fallback}</a>'
        lines.append(f"{medal[idx]} ${bid.amount} — {actor}")
    return "\n".join(lines)


def _human_time_left(ends_at: datetime | None) -> str:
    if ends_at is None:
        return "-"

    now = datetime.now(UTC)
    delta = ends_at - now
    if delta.total_seconds() <= 0:
        return "Завершен"

    total_seconds = int(delta.total_seconds())
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours > 0:
        return f"{hours}ч {minutes}м"
    if minutes > 0:
        return f"{minutes}м {seconds}с"
    return f"{seconds}с"


def _internal_chat_link_id(chat_id: int) -> str | None:
    raw = str(abs(chat_id))
    if not raw.startswith("100"):
        return None
    suffix = raw[3:]
    return suffix if suffix else None


def resolve_auction_post_link(
    chat_id: int | None,
    message_id: int | None,
    username: str | None = None,
) -> str | None:
    if chat_id is None or message_id is None:
        return None
    normalized_username = (username or "").strip().lstrip("@")
    if normalized_username:
        return f"https://t.me/{normalized_username}/{message_id}"
    internal_id = _internal_chat_link_id(chat_id)
    if internal_id is None:
        return None
    return f"https://t.me/c/{internal_id}/{message_id}"


async def resolve_auction_post_url(
    bot: Bot,
    *,
    auction_id: uuid.UUID,
    preferred_chat_id: int | None = None,
    preferred_message_id: int | None = None,
    preferred_username: str | None = None,
) -> str | None:
    direct = resolve_auction_post_link(
        chat_id=preferred_chat_id,
        message_id=preferred_message_id,
        username=preferred_username,
    )
    if direct is not None:
        return direct

    async with SessionFactory() as session:
        post = await session.scalar(
            select(AuctionPost)
            .where(
                AuctionPost.auction_id == auction_id,
                AuctionPost.chat_id.is_not(None),
                AuctionPost.message_id.is_not(None),
            )
            .order_by(AuctionPost.id.desc())
            .limit(1)
        )

    if post is None:
        return None

    username: str | None = None
    if isinstance(post.chat_id, int):
        try:
            chat = await bot.get_chat(post.chat_id)
            raw_username = getattr(chat, "username", None)
            if isinstance(raw_username, str) and raw_username.strip():
                username = raw_username.strip()
        except TelegramAPIError:
            username = None

    return resolve_auction_post_link(
        chat_id=post.chat_id,
        message_id=post.message_id,
        username=username,
    )


def render_auction_caption(view: AuctionView, *, publish_pending: bool = False) -> str:
    status_text = {
        AuctionStatus.DRAFT: "Черновик",
        AuctionStatus.ACTIVE: "Активен",
        AuctionStatus.ENDED: "Завершен",
        AuctionStatus.BOUGHT_OUT: "Выкуплен",
        AuctionStatus.CANCELLED: "Отменен",
        AuctionStatus.FROZEN: "Заморожен",
    }[view.auction.status]

    description = html.escape(view.auction.description)
    if len(description) > 420:
        description = f"{description[:420]}..."

    anti_sniper_text = "вкл" if view.auction.anti_sniper_enabled else "выкл"

    ending_line = _format_dt(view.auction.ends_at)
    if view.auction.status == AuctionStatus.ACTIVE:
        ending_line = f"{ending_line} ({_human_time_left(view.auction.ends_at)})"

    pending_line = "\n⏳ Публикуется..." if publish_pending else ""

    lines = [
        f"<b>🔥 Аукцион #{str(view.auction.id)[:8]}</b>{pending_line}",
        "",
        f"📝 {description}",
        f"🎯 Статус: <b>{status_text}</b>",
        f"👤 Продавец: {_format_user_mention(view.seller)}",
        f"💸 Текущая ставка: <b>${view.current_price}</b>",
        f"🏁 Старт: ${view.auction.start_price}",
        f"💰 Выкуп: {'$' + str(view.auction.buyout_price) if view.auction.buyout_price is not None else 'нет'}",
        f"🛡 Антиснайпер: {anti_sniper_text}",
        f"⏰ Финиш: <b>{ending_line}</b>",
        "",
        "🏆 <b>Топ-3 ставок</b>",
        _format_top_bids(view.top_bids),
    ]

    if view.auction.status in {AuctionStatus.ENDED, AuctionStatus.BOUGHT_OUT}:
        lines.extend(["", f"Победитель: <b>{_format_user_mention(view.winner)}</b>"])

    return "\n".join(lines)[:1024]


async def create_draft_auction(
    session: AsyncSession,
    *,
    seller_user_id: int,
    photo_file_id: str,
    photo_file_ids: list[str] | None,
    description: str,
    start_price: int,
    buyout_price: int | None,
    min_step: int,
    duration_hours: int,
    anti_sniper_enabled: bool,
) -> Auction:
    auction = Auction(
        seller_user_id=seller_user_id,
        photo_file_id=photo_file_id,
        description=description,
        start_price=start_price,
        buyout_price=buyout_price,
        min_step=min_step,
        duration_hours=duration_hours,
        anti_sniper_enabled=anti_sniper_enabled,
        anti_sniper_max_extensions=settings.anti_sniper_max_extensions,
    )
    session.add(auction)
    await session.flush()

    raw_photo_ids = (photo_file_ids or [photo_file_id])[:10]
    normalized_photo_ids: list[str] = []
    seen: set[str] = set()
    for item in raw_photo_ids:
        if item and item not in seen:
            normalized_photo_ids.append(item)
            seen.add(item)
    if not normalized_photo_ids:
        normalized_photo_ids = [photo_file_id]

    for index, file_id in enumerate(normalized_photo_ids):
        session.add(AuctionPhoto(auction_id=auction.id, file_id=file_id, position=index))

    await session.flush()
    return auction


async def load_auction_photo_ids(session: AsyncSession, auction_id: uuid.UUID) -> list[str]:
    rows = await session.execute(
        select(AuctionPhoto.file_id)
        .where(AuctionPhoto.auction_id == auction_id)
        .order_by(AuctionPhoto.position.asc(), AuctionPhoto.id.asc())
    )
    return list(rows.scalars().all())


async def get_auction_by_id(
    session: AsyncSession,
    auction_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Auction | None:
    stmt: Select[tuple[Auction]] = select(Auction).where(Auction.id == auction_id)
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


async def _top_bids_for_auction(session: AsyncSession, auction_id: uuid.UUID, limit: int = 3) -> list[TopBidView]:
    stmt = (
        select(Bid, User)
        .join(User, User.id == Bid.user_id)
        .where(Bid.auction_id == auction_id, Bid.is_removed.is_(False))
        .order_by(Bid.amount.desc(), Bid.created_at.asc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        TopBidView(
            amount=bid.amount,
            user_id=bid.user_id,
            tg_user_id=user.tg_user_id,
            username=user.username,
            first_name=user.first_name,
            created_at=bid.created_at,
        )
        for bid, user in rows
    ]


async def load_auction_view(session: AsyncSession, auction_id: uuid.UUID) -> AuctionView | None:
    auction = await get_auction_by_id(session, auction_id)
    if auction is None:
        return None

    seller = await session.scalar(select(User).where(User.id == auction.seller_user_id))
    if seller is None:
        return None

    top_bids = await _top_bids_for_auction(session, auction_id, limit=3)
    winner: User | None = None
    if auction.winner_user_id is not None:
        winner = await session.scalar(select(User).where(User.id == auction.winner_user_id))

    current_price = top_bids[0].amount if top_bids else auction.start_price
    minimum_next_bid = current_price + auction.min_step

    open_complaints = (
        await session.scalar(
            select(func.count(Complaint.id)).where(
                Complaint.auction_id == auction.id,
                Complaint.status == "OPEN",
            )
        )
    ) or 0

    photo_count = (
        await session.scalar(
            select(func.count(AuctionPhoto.id)).where(AuctionPhoto.auction_id == auction.id)
        )
    ) or 0
    if photo_count <= 0:
        photo_count = 1

    return AuctionView(
        auction=auction,
        seller=seller,
        winner=winner,
        top_bids=top_bids,
        current_price=current_price,
        minimum_next_bid=minimum_next_bid,
        open_complaints=int(open_complaints),
        photo_count=int(photo_count),
    )


async def activate_auction_inline_post(
    session: AsyncSession,
    *,
    auction_id: uuid.UUID,
    publisher_user_id: int,
    inline_message_id: str,
) -> Auction | None:
    auction = await get_auction_by_id(session, auction_id, for_update=True)
    if auction is None:
        return None

    if auction.status == AuctionStatus.DRAFT:
        now = datetime.now(UTC)
        auction.starts_at = now
        auction.ends_at = ceil_to_next_hour(now + timedelta(hours=auction.duration_hours))
        auction.status = AuctionStatus.ACTIVE
        auction.updated_at = now

    existing = await session.scalar(
        select(AuctionPost).where(AuctionPost.inline_message_id == inline_message_id)
    )
    if existing is None:
        session.add(
            AuctionPost(
                auction_id=auction.id,
                inline_message_id=inline_message_id,
                published_by_user_id=publisher_user_id,
            )
        )

    await session.flush()
    return auction


async def activate_auction_chat_post(
    session: AsyncSession,
    *,
    auction_id: uuid.UUID,
    publisher_user_id: int,
    chat_id: int,
    message_id: int,
) -> Auction | None:
    auction = await get_auction_by_id(session, auction_id, for_update=True)
    if auction is None:
        return None

    if auction.status != AuctionStatus.DRAFT:
        return None

    now = datetime.now(UTC)
    auction.starts_at = now
    auction.ends_at = ceil_to_next_hour(now + timedelta(hours=auction.duration_hours))
    auction.status = AuctionStatus.ACTIVE
    auction.updated_at = now

    existing = await session.scalar(
        select(AuctionPost).where(
            AuctionPost.chat_id == chat_id,
            AuctionPost.message_id == message_id,
        )
    )
    if existing is None:
        session.add(
            AuctionPost(
                auction_id=auction.id,
                chat_id=chat_id,
                message_id=message_id,
                published_by_user_id=publisher_user_id,
            )
        )

    await session.flush()
    return auction


async def _finalize_auction_locked(
    session: AsyncSession,
    auction: Auction,
    *,
    status: AuctionStatus,
    winner_user_id: int | None = None,
) -> FinalizeResult | None:
    if auction.status not in {AuctionStatus.ACTIVE, AuctionStatus.FROZEN}:
        return None

    if winner_user_id is None:
        top_bids = await _top_bids_for_auction(session, auction.id, limit=1)
        winner_user_id = top_bids[0].user_id if top_bids else None

    now = datetime.now(UTC)
    auction.status = status
    auction.winner_user_id = winner_user_id
    if auction.ends_at is None or auction.ends_at > now:
        auction.ends_at = now
    auction.updated_at = now

    seller = await session.scalar(select(User).where(User.id == auction.seller_user_id))
    winner_tg_user_id: int | None = None
    if winner_user_id is not None:
        winner = await session.scalar(select(User).where(User.id == winner_user_id))
        winner_tg_user_id = winner.tg_user_id if winner is not None else None

    if seller is None:
        return None

    return FinalizeResult(
        auction_id=auction.id,
        winner_tg_user_id=winner_tg_user_id,
        seller_tg_user_id=seller.tg_user_id,
    )


async def process_bid_action(
    session: AsyncSession,
    *,
    auction_id: uuid.UUID,
    bidder_user_id: int,
    multiplier: int,
    is_buyout: bool,
) -> BidActionResult:
    now = datetime.now(UTC)
    auction = await get_auction_by_id(session, auction_id, for_update=True)
    if auction is None:
        return BidActionResult(False, False, "Аукцион не найден")

    if auction.status != AuctionStatus.ACTIVE:
        return BidActionResult(False, True, "Аукцион не активен")

    if auction.ends_at is not None and now >= auction.ends_at:
        finalized = await _finalize_auction_locked(session, auction, status=AuctionStatus.ENDED)
        winner_tg_user_id = finalized.winner_tg_user_id if finalized else None
        seller_tg_user_id = finalized.seller_tg_user_id if finalized else None
        return BidActionResult(
            success=False,
            should_refresh=True,
            alert_text="Аукцион уже завершен",
            winner_tg_user_id=winner_tg_user_id,
            seller_tg_user_id=seller_tg_user_id,
            auction_finished=True,
        )

    if auction.seller_user_id == bidder_user_id:
        return BidActionResult(False, False, "Продавец не может ставить на свой лот")

    is_blacklisted = await session.scalar(
        select(BlacklistEntry.id).where(
            BlacklistEntry.user_id == bidder_user_id,
            BlacklistEntry.is_active.is_(True),
            (BlacklistEntry.expires_at.is_(None) | (BlacklistEntry.expires_at > now)),
        )
    )
    if is_blacklisted is not None:
        return BidActionResult(False, False, "Вы заблокированы и не можете делать ставки")

    top_bids = await _top_bids_for_auction(session, auction.id, limit=1)
    current_price = top_bids[0].amount if top_bids else auction.start_price
    leader_user_id = top_bids[0].user_id if top_bids else None
    outbid_tg_user_id = top_bids[0].tg_user_id if top_bids else None

    if leader_user_id == bidder_user_id:
        return BidActionResult(False, False, "Вы уже лидируете")

    buyout_triggered = is_buyout
    if is_buyout:
        if auction.buyout_price is None:
            return BidActionResult(False, False, "Для этого лота выкуп отключен")
        bid_amount = auction.buyout_price
    else:
        bid_amount = current_price + auction.min_step * multiplier
        if auction.buyout_price is not None and bid_amount >= auction.buyout_price:
            bid_amount = auction.buyout_price
            buyout_triggered = True

    duplicate_stmt = select(Bid.id).where(
        Bid.auction_id == auction.id,
        Bid.user_id == bidder_user_id,
        Bid.is_removed.is_(False),
        Bid.amount == bid_amount,
        Bid.created_at >= now - timedelta(seconds=settings.duplicate_bid_window_seconds),
    )
    duplicate = await session.scalar(duplicate_stmt)
    if duplicate is not None:
        return BidActionResult(False, False, "Эта ставка уже отправлена недавно")

    created_bid = Bid(auction_id=auction.id, user_id=bidder_user_id, amount=bid_amount)
    session.add(created_bid)
    await session.flush()

    fraud_signal_id = await evaluate_and_store_bid_fraud_signal(
        session,
        auction_id=auction.id,
        user_id=bidder_user_id,
        bid_id=created_bid.id,
    )

    winner_tg_user_id: int | None = None
    seller_tg_user_id: int | None = None

    if buyout_triggered:
        finalized = await _finalize_auction_locked(
            session,
            auction,
            status=AuctionStatus.BOUGHT_OUT,
            winner_user_id=bidder_user_id,
        )
        if finalized is not None:
            winner_tg_user_id = finalized.winner_tg_user_id
            seller_tg_user_id = finalized.seller_tg_user_id
        return BidActionResult(
            success=True,
            should_refresh=True,
            alert_text=f"Выкуп оформлен за ${bid_amount}",
            winner_tg_user_id=winner_tg_user_id,
            seller_tg_user_id=seller_tg_user_id,
            auction_finished=True,
            placed_bid_amount=bid_amount,
            outbid_tg_user_id=outbid_tg_user_id,
            created_bid_id=created_bid.id,
            fraud_signal_id=fraud_signal_id,
        )

    if (
        auction.anti_sniper_enabled
        and auction.ends_at is not None
        and auction.anti_sniper_extensions_used < auction.anti_sniper_max_extensions
        and auction.ends_at - now <= timedelta(minutes=settings.anti_sniper_window_minutes)
    ):
        auction.ends_at = auction.ends_at + timedelta(minutes=settings.anti_sniper_extend_minutes)
        auction.anti_sniper_extensions_used += 1

    auction.updated_at = now

    return BidActionResult(
        success=True,
        should_refresh=True,
        alert_text=f"Ставка принята: ${bid_amount}",
        placed_bid_amount=bid_amount,
        outbid_tg_user_id=outbid_tg_user_id,
        created_bid_id=created_bid.id,
        fraud_signal_id=fraud_signal_id,
    )


async def refresh_auction_posts(bot: Bot, auction_id: uuid.UUID) -> None:
    async with SessionFactory() as session:
        view = await load_auction_view(session, auction_id)
        if view is None:
            return

        posts = (
            await session.execute(
                select(AuctionPost).where(AuctionPost.auction_id == auction_id)
            )
        ).scalars().all()

    caption = render_auction_caption(view)
    reply_markup = None
    if view.auction.status == AuctionStatus.ACTIVE:
        reply_markup = auction_active_keyboard(
            auction_id=str(view.auction.id),
            min_step=view.auction.min_step,
            has_buyout=view.auction.buyout_price is not None,
        )

    for post in posts:
        await _refresh_auction_post_message(
            bot,
            post=post,
            caption=caption,
            reply_markup=reply_markup,
            photo_file_id=view.auction.photo_file_id,
        )


def _is_not_modified_error(exc: TelegramBadRequest) -> bool:
    return "message is not modified" in str(exc).lower()


def _should_upgrade_text_post(exc: TelegramBadRequest) -> bool:
    message = str(exc).lower()
    return (
        "there is no caption" in message
        or "message is not a media message" in message
        or "wrong message content type" in message
    )


async def _edit_post_media(
    bot: Bot,
    *,
    post: AuctionPost,
    media: InputMediaPhoto,
    reply_markup: InlineKeyboardMarkup | None,
) -> None:
    if post.inline_message_id:
        await bot.edit_message_media(
            inline_message_id=post.inline_message_id,
            media=media,
            reply_markup=reply_markup,
        )
        return
    if post.chat_id is not None and post.message_id is not None:
        await bot.edit_message_media(
            chat_id=post.chat_id,
            message_id=post.message_id,
            media=media,
            reply_markup=reply_markup,
        )


async def _refresh_auction_post_message(
    bot: Bot,
    *,
    post: AuctionPost,
    caption: str,
    reply_markup: InlineKeyboardMarkup | None,
    photo_file_id: str,
) -> None:
    try:
        if post.inline_message_id:
            await bot.edit_message_caption(
                inline_message_id=post.inline_message_id,
                caption=caption,
                reply_markup=reply_markup,
            )
            return
        if post.chat_id is not None and post.message_id is not None:
            await bot.edit_message_caption(
                chat_id=post.chat_id,
                message_id=post.message_id,
                caption=caption,
                reply_markup=reply_markup,
            )
            return
        return
    except TelegramBadRequest as exc:
        if _is_not_modified_error(exc):
            return
        if not _should_upgrade_text_post(exc):
            logger.warning("Failed to refresh auction post %s: %s", post.id, exc)
            return
    except TelegramForbiddenError as exc:
        logger.warning("No rights to edit auction post %s: %s", post.id, exc)
        return
    except TelegramRetryAfter as exc:
        logger.warning(
            "Rate limited while refreshing auction post %s (retry_after=%s): %s",
            post.id,
            exc.retry_after,
            exc,
        )
        return
    except (TelegramNetworkError, TelegramServerError) as exc:
        logger.warning("Transient error while refreshing auction post %s: %s", post.id, exc)
        return
    except TelegramAPIError as exc:
        logger.warning("Unexpected Telegram API error while refreshing auction post %s: %s", post.id, exc)
        return

    media = InputMediaPhoto(media=photo_file_id, caption=caption, parse_mode=ParseMode.HTML)
    try:
        await _edit_post_media(bot, post=post, media=media, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if _is_not_modified_error(exc):
            return
        logger.warning("Failed to attach media while refreshing auction post %s: %s", post.id, exc)
    except TelegramForbiddenError as exc:
        logger.warning("No rights to attach media for auction post %s: %s", post.id, exc)
    except TelegramRetryAfter as exc:
        logger.warning(
            "Rate limited while attaching media for auction post %s (retry_after=%s): %s",
            post.id,
            exc.retry_after,
            exc,
        )
    except (TelegramNetworkError, TelegramServerError) as exc:
        logger.warning("Transient error while attaching media for auction post %s: %s", post.id, exc)
    except TelegramAPIError as exc:
        logger.warning("Unexpected Telegram API error while attaching media for auction post %s: %s", post.id, exc)


async def _safe_refresh_auction_posts(bot: Bot, auction_id: uuid.UUID) -> None:
    try:
        await refresh_auction_posts(bot, auction_id)
    except Exception as exc:
        logger.exception("Failed to refresh auction %s posts after finalize: %s", auction_id, exc)


async def finalize_expired_auctions(bot: Bot) -> int:
    now = datetime.now(UTC)
    async with SessionFactory() as session:
        auction_ids = (
            await session.execute(
                select(Auction.id).where(
                    Auction.status == AuctionStatus.ACTIVE,
                    Auction.ends_at.is_not(None),
                    Auction.ends_at <= now,
                )
            )
        ).scalars().all()

    finalized_results: list[FinalizeResult] = []
    for auction_id in auction_ids:
        async with SessionFactory() as session:
            async with session.begin():
                auction = await get_auction_by_id(session, auction_id, for_update=True)
                if auction is None or auction.status != AuctionStatus.ACTIVE:
                    continue
                finalized = await _finalize_auction_locked(session, auction, status=AuctionStatus.ENDED)
                if finalized is not None:
                    finalized_results.append(finalized)

        await _safe_refresh_auction_posts(bot, auction_id)

    for result in finalized_results:
        post_url = await resolve_auction_post_url(bot, auction_id=result.auction_id)
        reply_markup = open_auction_post_keyboard(post_url) if post_url else None
        await send_user_topic_message(
            bot,
            tg_user_id=result.seller_tg_user_id,
            purpose=PrivateTopicPurpose.AUCTIONS,
            text=f"Аукцион #{str(result.auction_id)[:8]} завершен.",
            reply_markup=reply_markup,
            message_effect_id=resolve_auction_message_effect_id(
                AuctionMessageEffectEvent.ENDED_SELLER
            ),
            notification_event=NotificationEventType.AUCTION_FINISH,
        )

        if result.winner_tg_user_id is not None:
            await send_user_topic_message(
                bot,
                tg_user_id=result.winner_tg_user_id,
                purpose=PrivateTopicPurpose.AUCTIONS,
                text=f"Вы победили в аукционе #{str(result.auction_id)[:8]}.",
                reply_markup=reply_markup,
                message_effect_id=resolve_auction_message_effect_id(
                    AuctionMessageEffectEvent.ENDED_WINNER
                ),
                notification_event=NotificationEventType.AUCTION_WIN,
            )

    return len(finalized_results)
