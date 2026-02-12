from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Auction, Bid, Complaint, FraudSignal, ModerationLog, User


@dataclass(slots=True)
class AuctionTimelineItem:
    happened_at: datetime
    source: str
    title: str
    details: str


TIMELINE_SOURCE_AUCTION = "auction"
TIMELINE_SOURCE_BID = "bid"
TIMELINE_SOURCE_COMPLAINT = "complaint"
TIMELINE_SOURCE_FRAUD = "fraud"
TIMELINE_SOURCE_MODERATION = "moderation"

TIMELINE_SOURCES = frozenset(
    {
        TIMELINE_SOURCE_AUCTION,
        TIMELINE_SOURCE_BID,
        TIMELINE_SOURCE_COMPLAINT,
        TIMELINE_SOURCE_FRAUD,
        TIMELINE_SOURCE_MODERATION,
    }
)


def _label_user(users_by_id: dict[int, User], user_id: int | None) -> str:
    if user_id is None:
        return "-"
    user = users_by_id.get(user_id)
    if user is None:
        return f"uid:{user_id}"
    if user.username:
        return f"@{user.username}"
    return str(user.tg_user_id)


def _timeline_order_rank(item: AuctionTimelineItem) -> int:
    if item.title in {"Аукцион создан", "Аукцион опубликован"}:
        return 10
    if item.title in {"Ставка принята", "Ставка снята"}:
        return 20
    if item.title in {"Жалоба создана", "Фрод-сигнал создан"}:
        return 30
    if item.source == "moderation":
        return 40
    if item.title in {"Жалоба обработана", "Фрод-сигнал обработан"}:
        return 50
    return 60


def _normalize_sources(sources: Iterable[str] | None) -> set[str]:
    if sources is None:
        return set(TIMELINE_SOURCES)

    normalized = {value.strip().lower() for value in sources if value.strip()}
    if not normalized:
        return set(TIMELINE_SOURCES)

    invalid = sorted(normalized - TIMELINE_SOURCES)
    if invalid:
        allowed = ", ".join(sorted(TIMELINE_SOURCES))
        invalid_label = ", ".join(invalid)
        raise ValueError(f"Unknown timeline source filter: {invalid_label}. Allowed: {allowed}")
    return normalized


async def _count_timeline_events(
    session: AsyncSession,
    auction: Auction,
    included_sources: set[str],
) -> int:
    total = 0

    if TIMELINE_SOURCE_AUCTION in included_sources:
        total += 1
        if auction.starts_at is not None:
            total += 1

    if TIMELINE_SOURCE_BID in included_sources:
        total += int(
            await session.scalar(select(func.count(Bid.id)).where(Bid.auction_id == auction.id)) or 0
        )

    if TIMELINE_SOURCE_COMPLAINT in included_sources:
        total += int(
            await session.scalar(select(func.count(Complaint.id)).where(Complaint.auction_id == auction.id)) or 0
        )
        total += int(
            await session.scalar(
                select(func.count(Complaint.id)).where(
                    Complaint.auction_id == auction.id,
                    Complaint.resolved_at.is_not(None),
                )
            )
            or 0
        )

    if TIMELINE_SOURCE_FRAUD in included_sources:
        total += int(
            await session.scalar(select(func.count(FraudSignal.id)).where(FraudSignal.auction_id == auction.id))
            or 0
        )
        total += int(
            await session.scalar(
                select(func.count(FraudSignal.id)).where(
                    FraudSignal.auction_id == auction.id,
                    FraudSignal.resolved_at.is_not(None),
                )
            )
            or 0
        )

    if TIMELINE_SOURCE_MODERATION in included_sources:
        total += int(
            await session.scalar(
                select(func.count(ModerationLog.id)).where(ModerationLog.auction_id == auction.id)
            )
            or 0
        )

    return total


async def _build_timeline_events(
    session: AsyncSession,
    auction: Auction,
    included_sources: set[str],
    *,
    max_per_source: int | None,
) -> list[AuctionTimelineItem]:
    bids: list[Bid] = []
    complaints: list[Complaint] = []
    signals: list[FraudSignal] = []
    mod_logs: list[ModerationLog] = []

    if TIMELINE_SOURCE_BID in included_sources:
        bid_query = (
            select(Bid)
            .where(Bid.auction_id == auction.id)
            .order_by(Bid.created_at.asc(), Bid.id.asc())
        )
        if max_per_source is not None:
            bid_query = bid_query.limit(max_per_source)
        bids = list((await session.execute(bid_query)).scalars().all())

    if TIMELINE_SOURCE_COMPLAINT in included_sources:
        complaint_query = (
            select(Complaint)
            .where(Complaint.auction_id == auction.id)
            .order_by(Complaint.created_at.asc(), Complaint.id.asc())
        )
        if max_per_source is not None:
            complaint_query = complaint_query.limit(max_per_source)
        complaints = list((await session.execute(complaint_query)).scalars().all())

    if TIMELINE_SOURCE_FRAUD in included_sources:
        signal_query = (
            select(FraudSignal)
            .where(FraudSignal.auction_id == auction.id)
            .order_by(FraudSignal.created_at.asc(), FraudSignal.id.asc())
        )
        if max_per_source is not None:
            signal_query = signal_query.limit(max_per_source)
        signals = list((await session.execute(signal_query)).scalars().all())

    if TIMELINE_SOURCE_MODERATION in included_sources:
        moderation_query = (
            select(ModerationLog)
            .where(ModerationLog.auction_id == auction.id)
            .order_by(ModerationLog.created_at.asc(), ModerationLog.id.asc())
        )
        if max_per_source is not None:
            moderation_query = moderation_query.limit(max_per_source)
        mod_logs = list((await session.execute(moderation_query)).scalars().all())

    user_ids: set[int] = set()
    if TIMELINE_SOURCE_AUCTION in included_sources:
        user_ids.add(auction.seller_user_id)
        if auction.winner_user_id is not None:
            user_ids.add(auction.winner_user_id)
    for item in bids:
        user_ids.add(item.user_id)
        if item.removed_by_user_id is not None:
            user_ids.add(item.removed_by_user_id)
    for item in complaints:
        user_ids.add(item.reporter_user_id)
        if item.target_user_id is not None:
            user_ids.add(item.target_user_id)
        if item.resolved_by_user_id is not None:
            user_ids.add(item.resolved_by_user_id)
    for item in signals:
        user_ids.add(item.user_id)
        if item.resolved_by_user_id is not None:
            user_ids.add(item.resolved_by_user_id)
    for item in mod_logs:
        user_ids.add(item.actor_user_id)
        if item.target_user_id is not None:
            user_ids.add(item.target_user_id)

    users_by_id: dict[int, User] = {}
    if user_ids:
        users = (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
        users_by_id = {user.id: user for user in users}

    timeline: list[AuctionTimelineItem] = []

    if TIMELINE_SOURCE_AUCTION in included_sources:
        timeline.append(
            AuctionTimelineItem(
                happened_at=auction.created_at,
                source="auction",
                title="Аукцион создан",
                details=(
                    f"seller={_label_user(users_by_id, auction.seller_user_id)}, "
                    f"start=${auction.start_price}, step=${auction.min_step}"
                ),
            )
        )

        if auction.starts_at is not None:
            timeline.append(
                AuctionTimelineItem(
                    happened_at=auction.starts_at,
                    source="auction",
                    title="Аукцион опубликован",
                    details=f"status={auction.status}",
                )
            )

    for item in bids:
        title = "Ставка принята" if not item.is_removed else "Ставка снята"
        details = f"bid={item.id}, amount=${item.amount}, user={_label_user(users_by_id, item.user_id)}"
        if item.is_removed:
            details += (
                f", by={_label_user(users_by_id, item.removed_by_user_id)},"
                f" reason={item.removed_reason or '-'}"
            )
        timeline.append(
            AuctionTimelineItem(
                happened_at=item.created_at,
                source="bid",
                title=title,
                details=details,
            )
        )

    for item in complaints:
        timeline.append(
            AuctionTimelineItem(
                happened_at=item.created_at,
                source="complaint",
                title="Жалоба создана",
                details=(
                    f"complaint={item.id}, reporter={_label_user(users_by_id, item.reporter_user_id)},"
                    f" target={_label_user(users_by_id, item.target_user_id)}, reason={item.reason}"
                ),
            )
        )
        if item.resolved_at is not None:
            timeline.append(
                AuctionTimelineItem(
                    happened_at=item.resolved_at,
                    source="complaint",
                    title="Жалоба обработана",
                    details=(
                        f"complaint={item.id}, status={item.status}, resolver={_label_user(users_by_id, item.resolved_by_user_id)},"
                        f" note={item.resolution_note or '-'}"
                    ),
                )
            )

    for item in signals:
        timeline.append(
            AuctionTimelineItem(
                happened_at=item.created_at,
                source="fraud",
                title="Фрод-сигнал создан",
                details=(
                    f"signal={item.id}, user={_label_user(users_by_id, item.user_id)}, score={item.score},"
                    f" status={item.status}"
                ),
            )
        )
        if item.resolved_at is not None:
            timeline.append(
                AuctionTimelineItem(
                    happened_at=item.resolved_at,
                    source="fraud",
                    title="Фрод-сигнал обработан",
                    details=(
                        f"signal={item.id}, status={item.status}, resolver={_label_user(users_by_id, item.resolved_by_user_id)},"
                        f" note={item.resolution_note or '-'}"
                    ),
                )
            )

    for item in mod_logs:
        timeline.append(
            AuctionTimelineItem(
                happened_at=item.created_at,
                source="moderation",
                title=f"Мод-действие: {item.action}",
                details=(
                    f"actor={_label_user(users_by_id, item.actor_user_id)},"
                    f" target={_label_user(users_by_id, item.target_user_id)}, reason={item.reason}"
                ),
            )
        )

    timeline.sort(
        key=lambda item: (
            item.happened_at,
            _timeline_order_rank(item),
        )
    )
    return timeline


async def build_auction_timeline_page(
    session: AsyncSession,
    auction_id: uuid.UUID,
    *,
    page: int,
    limit: int,
    sources: Iterable[str] | None = None,
) -> tuple[Auction | None, list[AuctionTimelineItem], int]:
    if page < 0:
        raise ValueError("Page must be >= 0")
    if limit < 1:
        raise ValueError("Limit must be >= 1")

    auction = await session.scalar(select(Auction).where(Auction.id == auction_id))
    if auction is None:
        return None, [], 0

    included_sources = _normalize_sources(sources)
    total_items = await _count_timeline_events(session, auction, included_sources)

    if total_items == 0:
        return auction, [], 0

    fetch_limit = (page + 1) * limit
    timeline = await _build_timeline_events(
        session,
        auction,
        included_sources,
        max_per_source=fetch_limit,
    )
    offset = page * limit
    return auction, timeline[offset : offset + limit], total_items


async def build_auction_timeline(
    session: AsyncSession,
    auction_id: uuid.UUID,
) -> tuple[Auction | None, list[AuctionTimelineItem]]:
    auction = await session.scalar(select(Auction).where(Auction.id == auction_id))
    if auction is None:
        return None, []
    timeline = await _build_timeline_events(
        session,
        auction,
        set(TIMELINE_SOURCES),
        max_per_source=None,
    )
    return auction, timeline
