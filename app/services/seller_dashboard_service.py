from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import AuctionStatus
from app.db.models import Auction, AuctionPost, Bid, User


@dataclass(slots=True)
class SellerAuctionListItem:
    auction_id: uuid.UUID
    status: AuctionStatus
    start_price: int
    current_price: int
    bid_count: int
    ends_at: datetime | None
    created_at: datetime


@dataclass(slots=True)
class SellerAuctionPostItem:
    chat_id: int | None
    message_id: int | None
    inline_message_id: str | None


@dataclass(slots=True)
class SellerBidLogItem:
    bid_id: uuid.UUID
    amount: int
    created_at: datetime
    tg_user_id: int
    username: str | None
    is_removed: bool


_FILTER_STATUSES: dict[str, tuple[AuctionStatus, ...] | None] = {
    "a": (AuctionStatus.ACTIVE, AuctionStatus.FROZEN),
    "f": (AuctionStatus.ENDED, AuctionStatus.BOUGHT_OUT, AuctionStatus.CANCELLED),
    "d": (AuctionStatus.DRAFT,),
    "l": None,
}


def is_valid_my_auctions_filter(filter_key: str) -> bool:
    return filter_key in _FILTER_STATUSES


async def list_seller_auctions(
    session: AsyncSession,
    *,
    seller_user_id: int,
    filter_key: str,
    page: int,
    page_size: int,
) -> tuple[list[SellerAuctionListItem], int]:
    statuses = _FILTER_STATUSES.get(filter_key)

    bid_stats_subquery = (
        select(
            Bid.auction_id.label("auction_id"),
            func.max(Bid.amount).label("max_bid"),
            func.count(Bid.id).label("bid_count"),
        )
        .where(Bid.is_removed.is_(False))
        .group_by(Bid.auction_id)
        .subquery()
    )

    count_stmt = select(func.count(Auction.id)).where(Auction.seller_user_id == seller_user_id)
    if statuses is not None:
        count_stmt = count_stmt.where(Auction.status.in_(statuses))
    total_items = int((await session.scalar(count_stmt)) or 0)

    list_stmt = (
        select(
            Auction.id,
            Auction.status,
            Auction.start_price,
            func.coalesce(bid_stats_subquery.c.max_bid, Auction.start_price),
            func.coalesce(bid_stats_subquery.c.bid_count, 0),
            Auction.ends_at,
            Auction.created_at,
        )
        .outerjoin(bid_stats_subquery, bid_stats_subquery.c.auction_id == Auction.id)
        .where(Auction.seller_user_id == seller_user_id)
        .order_by(Auction.created_at.desc())
        .limit(page_size)
        .offset(page * page_size)
    )
    if statuses is not None:
        list_stmt = list_stmt.where(Auction.status.in_(statuses))

    rows = (await session.execute(list_stmt)).all()
    items = [
        SellerAuctionListItem(
            auction_id=auction_id,
            status=status,
            start_price=start_price,
            current_price=current_price,
            bid_count=int(bid_count),
            ends_at=ends_at,
            created_at=created_at,
        )
        for auction_id, status, start_price, current_price, bid_count, ends_at, created_at in rows
    ]
    return items, total_items


async def load_seller_auction(
    session: AsyncSession,
    *,
    seller_user_id: int,
    auction_id: uuid.UUID,
) -> SellerAuctionListItem | None:
    bid_stats_subquery = (
        select(
            Bid.auction_id.label("auction_id"),
            func.max(Bid.amount).label("max_bid"),
            func.count(Bid.id).label("bid_count"),
        )
        .where(Bid.is_removed.is_(False))
        .group_by(Bid.auction_id)
        .subquery()
    )
    stmt = (
        select(
            Auction.id,
            Auction.status,
            Auction.start_price,
            func.coalesce(bid_stats_subquery.c.max_bid, Auction.start_price),
            func.coalesce(bid_stats_subquery.c.bid_count, 0),
            Auction.ends_at,
            Auction.created_at,
        )
        .outerjoin(bid_stats_subquery, bid_stats_subquery.c.auction_id == Auction.id)
        .where(Auction.id == auction_id, Auction.seller_user_id == seller_user_id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None

    (
        loaded_auction_id,
        status,
        start_price,
        current_price,
        bid_count,
        ends_at,
        created_at,
    ) = row
    return SellerAuctionListItem(
        auction_id=loaded_auction_id,
        status=status,
        start_price=start_price,
        current_price=current_price,
        bid_count=int(bid_count),
        ends_at=ends_at,
        created_at=created_at,
    )


async def list_seller_auction_posts(
    session: AsyncSession,
    *,
    seller_user_id: int,
    auction_id: uuid.UUID,
) -> list[SellerAuctionPostItem]:
    stmt = (
        select(AuctionPost)
        .join(Auction, Auction.id == AuctionPost.auction_id)
        .where(Auction.id == auction_id, Auction.seller_user_id == seller_user_id)
        .order_by(AuctionPost.id.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        SellerAuctionPostItem(
            chat_id=row.chat_id,
            message_id=row.message_id,
            inline_message_id=row.inline_message_id,
        )
        for row in rows
    ]


async def list_seller_auction_bid_logs(
    session: AsyncSession,
    *,
    seller_user_id: int,
    auction_id: uuid.UUID,
    limit: int = 15,
) -> list[SellerBidLogItem]:
    seller_check = await session.scalar(
        select(Auction.id).where(Auction.id == auction_id, Auction.seller_user_id == seller_user_id)
    )
    if seller_check is None:
        return []

    stmt = (
        select(Bid, User)
        .join(User, User.id == Bid.user_id)
        .where(Bid.auction_id == auction_id)
        .order_by(Bid.created_at.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        SellerBidLogItem(
            bid_id=bid.id,
            amount=bid.amount,
            created_at=bid.created_at,
            tg_user_id=user.tg_user_id,
            username=user.username,
            is_removed=bid.is_removed,
        )
        for bid, user in rows
    ]
