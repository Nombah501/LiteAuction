from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import AuctionStatus, ModerationAction
from app.db.models import Auction, Complaint, FraudSignal, ModerationLog, User
from app.services.timeline_service import build_auction_timeline


@pytest.mark.asyncio
async def test_timeline_orders_moderation_before_complaint_resolution_on_same_timestamp(
    integration_engine,
) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    stamp = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=80101, username="seller")
            reporter = User(tg_user_id=80102, username="reporter")
            actor = User(tg_user_id=80103, username="mod")
            session.add_all([seller, reporter, actor])
            await session.flush()

            auction = Auction(
                seller_user_id=seller.id,
                description="lot",
                photo_file_id="photo",
                start_price=10,
                buyout_price=None,
                min_step=1,
                duration_hours=24,
                status=AuctionStatus.ACTIVE,
            )
            session.add(auction)
            await session.flush()

            session.add(
                Complaint(
                    auction_id=auction.id,
                    reporter_user_id=reporter.id,
                    reason="timeline order",
                    status="RESOLVED",
                    created_at=stamp,
                    resolved_at=stamp + timedelta(minutes=1),
                    resolved_by_user_id=actor.id,
                    resolution_note="ok",
                )
            )
            session.add(
                ModerationLog(
                    actor_user_id=actor.id,
                    auction_id=auction.id,
                    action=ModerationAction.FREEZE_AUCTION,
                    reason="timeline order",
                    created_at=stamp + timedelta(minutes=1),
                )
            )
            auction_id = auction.id

    async with session_factory() as session:
        _, timeline = await build_auction_timeline(session, auction_id)

    titles = [item.title for item in timeline]
    assert titles.index("Мод-действие: FREEZE_AUCTION") < titles.index("Жалоба обработана")


@pytest.mark.asyncio
async def test_timeline_orders_moderation_before_fraud_resolution_on_same_timestamp(
    integration_engine,
) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    stamp = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=80201, username="seller")
            suspect = User(tg_user_id=80202, username="suspect")
            actor = User(tg_user_id=80203, username="mod")
            session.add_all([seller, suspect, actor])
            await session.flush()

            auction = Auction(
                seller_user_id=seller.id,
                description="lot",
                photo_file_id="photo",
                start_price=10,
                buyout_price=None,
                min_step=1,
                duration_hours=24,
                status=AuctionStatus.ACTIVE,
            )
            session.add(auction)
            await session.flush()

            session.add(
                FraudSignal(
                    auction_id=auction.id,
                    user_id=suspect.id,
                    bid_id=None,
                    score=90,
                    reasons={"rules": [{"code": "TEST", "detail": "x", "score": 90}]},
                    status="CONFIRMED",
                    created_at=stamp,
                    resolved_at=stamp + timedelta(minutes=1),
                    resolved_by_user_id=actor.id,
                    resolution_note="ok",
                )
            )
            session.add(
                ModerationLog(
                    actor_user_id=actor.id,
                    target_user_id=suspect.id,
                    auction_id=auction.id,
                    action=ModerationAction.BAN_USER,
                    reason="timeline order",
                    created_at=stamp + timedelta(minutes=1),
                )
            )
            auction_id = auction.id

    async with session_factory() as session:
        _, timeline = await build_auction_timeline(session, auction_id)

    titles = [item.title for item in timeline]
    assert titles.index("Мод-действие: BAN_USER") < titles.index("Фрод-сигнал обработан")
