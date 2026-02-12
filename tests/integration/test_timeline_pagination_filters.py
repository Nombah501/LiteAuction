from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import AuctionStatus, ModerationAction
from app.db.models import Auction, Bid, Complaint, ModerationLog, User
from app.services.timeline_service import build_auction_timeline_page


@pytest.mark.asyncio
async def test_timeline_page_source_filter_returns_only_requested_source(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    stamp = datetime(2026, 2, 1, 10, 0, tzinfo=UTC)

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=91001, username="seller")
            reporter = User(tg_user_id=91002, username="reporter")
            actor = User(tg_user_id=91003, username="mod")
            session.add_all([seller, reporter, actor])
            await session.flush()

            auction = Auction(
                seller_user_id=seller.id,
                description="lot",
                photo_file_id="photo",
                start_price=100,
                buyout_price=None,
                min_step=10,
                duration_hours=24,
                status=AuctionStatus.ACTIVE,
                created_at=stamp,
            )
            session.add(auction)
            await session.flush()

            session.add(
                Complaint(
                    auction_id=auction.id,
                    reporter_user_id=reporter.id,
                    reason="reason",
                    status="RESOLVED",
                    created_at=stamp + timedelta(minutes=1),
                    resolved_at=stamp + timedelta(minutes=2),
                    resolved_by_user_id=actor.id,
                )
            )
            session.add(
                ModerationLog(
                    actor_user_id=actor.id,
                    auction_id=auction.id,
                    action=ModerationAction.FREEZE_AUCTION,
                    reason="review",
                    created_at=stamp + timedelta(minutes=2),
                )
            )
            auction_id = auction.id

    async with session_factory() as session:
        auction, page_items, total_items = await build_auction_timeline_page(
            session,
            auction_id,
            page=0,
            limit=50,
            sources=["moderation"],
        )

    assert auction is not None
    assert total_items == 1
    assert len(page_items) == 1
    assert page_items[0].source == "moderation"
    assert page_items[0].title == "Мод-действие: FREEZE_AUCTION"


@pytest.mark.asyncio
async def test_timeline_page_boundary_preserves_bid_order(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    stamp = datetime(2026, 2, 2, 10, 0, tzinfo=UTC)

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=91101, username="seller")
            bidder = User(tg_user_id=91102, username="bidder")
            session.add_all([seller, bidder])
            await session.flush()

            auction = Auction(
                seller_user_id=seller.id,
                description="lot",
                photo_file_id="photo",
                start_price=100,
                buyout_price=None,
                min_step=10,
                duration_hours=24,
                status=AuctionStatus.ACTIVE,
                created_at=stamp,
            )
            session.add(auction)
            await session.flush()

            for idx in range(5):
                session.add(
                    Bid(
                        auction_id=auction.id,
                        user_id=bidder.id,
                        amount=100 + idx,
                        created_at=stamp + timedelta(minutes=idx + 1),
                    )
                )

            auction_id = auction.id

    async with session_factory() as session:
        _, first_page, first_total = await build_auction_timeline_page(
            session,
            auction_id,
            page=0,
            limit=2,
            sources=["bid"],
        )
        _, third_page, third_total = await build_auction_timeline_page(
            session,
            auction_id,
            page=2,
            limit=2,
            sources=["bid"],
        )

    assert first_total == 5
    assert third_total == 5
    assert len(first_page) == 2
    assert len(third_page) == 1
    assert "amount=$100" in first_page[0].details
    assert "amount=$101" in first_page[1].details
    assert "amount=$104" in third_page[0].details


@pytest.mark.asyncio
async def test_timeline_page_beyond_total_returns_empty_page(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    stamp = datetime(2026, 2, 3, 10, 0, tzinfo=UTC)

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=91201, username="seller")
            bidder = User(tg_user_id=91202, username="bidder")
            session.add_all([seller, bidder])
            await session.flush()

            auction = Auction(
                seller_user_id=seller.id,
                description="lot",
                photo_file_id="photo",
                start_price=100,
                buyout_price=None,
                min_step=10,
                duration_hours=24,
                status=AuctionStatus.ACTIVE,
                created_at=stamp,
            )
            session.add(auction)
            await session.flush()

            for idx in range(3):
                session.add(
                    Bid(
                        auction_id=auction.id,
                        user_id=bidder.id,
                        amount=200 + idx,
                        created_at=stamp + timedelta(minutes=idx + 1),
                    )
                )

            auction_id = auction.id

    async with session_factory() as session:
        _, page_items, total_items = await build_auction_timeline_page(
            session,
            auction_id,
            page=3,
            limit=2,
            sources=["bid"],
        )

    assert total_items == 3
    assert page_items == []
