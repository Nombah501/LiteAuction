from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Auction, Bid, Complaint, FraudSignal, ModerationLog, User


@dataclass(slots=True)
class AuctionTimelineItem:
    happened_at: datetime
    source: str
    title: str
    details: str


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


async def build_auction_timeline(
    session: AsyncSession,
    auction_id: uuid.UUID,
) -> tuple[Auction | None, list[AuctionTimelineItem]]:
    auction = await session.scalar(select(Auction).where(Auction.id == auction_id))
    if auction is None:
        return None, []

    bids = (
        await session.execute(
            select(Bid)
            .where(Bid.auction_id == auction_id)
            .order_by(Bid.created_at.asc(), Bid.id.asc())
        )
    ).scalars().all()
    complaints = (
        await session.execute(
            select(Complaint)
            .where(Complaint.auction_id == auction_id)
            .order_by(Complaint.created_at.asc(), Complaint.id.asc())
        )
    ).scalars().all()
    signals = (
        await session.execute(
            select(FraudSignal)
            .where(FraudSignal.auction_id == auction_id)
            .order_by(FraudSignal.created_at.asc(), FraudSignal.id.asc())
        )
    ).scalars().all()
    mod_logs = (
        await session.execute(
            select(ModerationLog)
            .where(ModerationLog.auction_id == auction_id)
            .order_by(ModerationLog.created_at.asc(), ModerationLog.id.asc())
        )
    ).scalars().all()

    user_ids: set[int] = set()
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
    return auction, timeline
