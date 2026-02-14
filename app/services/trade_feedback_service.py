from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import AuctionStatus
from app.db.models import Auction, TradeFeedback, User


@dataclass(slots=True)
class TradeFeedbackSubmitResult:
    ok: bool
    message: str
    item: TradeFeedback | None = None
    created: bool = False
    updated: bool = False


@dataclass(slots=True)
class TradeFeedbackModerationResult:
    ok: bool
    message: str
    item: TradeFeedback | None = None
    changed: bool = False


@dataclass(slots=True, frozen=True)
class TradeFeedbackSummary:
    total_received: int
    visible_received: int
    hidden_received: int
    average_visible_rating: float | None


@dataclass(slots=True, frozen=True)
class TradeFeedbackReceivedView:
    item: TradeFeedback
    author: User
    auction: Auction


def _normalize_comment(comment: str | None) -> str | None:
    if comment is None:
        return None
    normalized = "\n".join(line.rstrip() for line in comment.strip().splitlines()).strip()
    if not normalized:
        return None
    if len(normalized) > 1000:
        normalized = normalized[:1000].rstrip()
    return normalized


async def submit_trade_feedback(
    session: AsyncSession,
    *,
    auction_id: uuid.UUID,
    author_user_id: int,
    rating: int,
    comment: str | None,
) -> TradeFeedbackSubmitResult:
    if rating < 1 or rating > 5:
        return TradeFeedbackSubmitResult(False, "Оценка должна быть от 1 до 5")

    auction = await session.scalar(select(Auction).where(Auction.id == auction_id).with_for_update())
    if auction is None:
        return TradeFeedbackSubmitResult(False, "Аукцион не найден")

    if auction.status not in {AuctionStatus.ENDED, AuctionStatus.BOUGHT_OUT}:
        return TradeFeedbackSubmitResult(False, "Оставить отзыв можно только по завершенному аукциону")

    if auction.winner_user_id is None:
        return TradeFeedbackSubmitResult(False, "У аукциона нет победителя, отзыв недоступен")

    if author_user_id == auction.seller_user_id:
        target_user_id = auction.winner_user_id
    elif author_user_id == auction.winner_user_id:
        target_user_id = auction.seller_user_id
    else:
        return TradeFeedbackSubmitResult(False, "Оставить отзыв могут только продавец и победитель")

    normalized_comment = _normalize_comment(comment)
    now = datetime.now(UTC)

    item = await session.scalar(
        select(TradeFeedback)
        .where(TradeFeedback.auction_id == auction.id, TradeFeedback.author_user_id == author_user_id)
        .with_for_update()
    )
    if item is not None:
        item.target_user_id = target_user_id
        item.rating = rating
        item.comment = normalized_comment
        item.updated_at = now
        return TradeFeedbackSubmitResult(True, "Отзыв обновлен", item=item, updated=True)

    created = TradeFeedback(
        auction_id=auction.id,
        author_user_id=author_user_id,
        target_user_id=target_user_id,
        rating=rating,
        comment=normalized_comment,
        status="VISIBLE",
        updated_at=now,
    )
    session.add(created)
    await session.flush()
    return TradeFeedbackSubmitResult(True, "Отзыв сохранен", item=created, created=True)


async def set_trade_feedback_visibility(
    session: AsyncSession,
    *,
    feedback_id: int,
    visible: bool,
    moderator_user_id: int,
    note: str,
) -> TradeFeedbackModerationResult:
    item = await session.scalar(select(TradeFeedback).where(TradeFeedback.id == feedback_id).with_for_update())
    if item is None:
        return TradeFeedbackModerationResult(False, "Отзыв не найден")

    target_status = "VISIBLE" if visible else "HIDDEN"
    if item.status == target_status:
        return TradeFeedbackModerationResult(True, "Статус уже установлен", item=item, changed=False)

    now = datetime.now(UTC)
    item.status = target_status
    item.moderator_user_id = moderator_user_id
    item.moderation_note = (note or "").strip() or None
    item.moderated_at = now
    item.updated_at = now
    return TradeFeedbackModerationResult(True, "Статус отзыва обновлен", item=item, changed=True)


async def get_trade_feedback_summary(
    session: AsyncSession,
    *,
    target_user_id: int,
) -> TradeFeedbackSummary:
    rows = (
        await session.execute(
            select(TradeFeedback.rating, TradeFeedback.status).where(TradeFeedback.target_user_id == target_user_id)
        )
    ).all()

    total_received = len(rows)
    visible_ratings = [int(rating) for rating, status in rows if status == "VISIBLE"]
    visible_received = len(visible_ratings)
    hidden_received = total_received - visible_received
    average_visible_rating = None
    if visible_ratings:
        average_visible_rating = sum(visible_ratings) / len(visible_ratings)

    return TradeFeedbackSummary(
        total_received=total_received,
        visible_received=visible_received,
        hidden_received=hidden_received,
        average_visible_rating=average_visible_rating,
    )


async def list_received_trade_feedback(
    session: AsyncSession,
    *,
    target_user_id: int,
    limit: int = 10,
) -> list[TradeFeedbackReceivedView]:
    author_user = User
    stmt = (
        select(TradeFeedback, author_user, Auction)
        .join(author_user, author_user.id == TradeFeedback.author_user_id)
        .join(Auction, Auction.id == TradeFeedback.auction_id)
        .where(TradeFeedback.target_user_id == target_user_id)
        .order_by(TradeFeedback.created_at.desc(), TradeFeedback.id.desc())
        .limit(max(limit, 1))
    )
    rows = (await session.execute(stmt)).all()
    return [TradeFeedbackReceivedView(item=item, author=author, auction=auction) for item, author, auction in rows]
