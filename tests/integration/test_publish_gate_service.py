from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import AuctionStatus, GuarantorRequestStatus
from app.db.models import Auction, Complaint, FraudSignal, GuarantorRequest, TelegramUserVerification, User
from app.services.publish_gate_service import evaluate_seller_publish_gate


def _make_auction(*, seller_user_id: int, description: str) -> Auction:
    return Auction(
        seller_user_id=seller_user_id,
        description=description,
        photo_file_id="photo",
        start_price=100,
        buyout_price=None,
        min_step=5,
        duration_hours=24,
        status=AuctionStatus.ACTIVE,
    )


@pytest.mark.asyncio
async def test_publish_gate_allows_low_risk_user(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=93901, username="low_risk_seller")
            session.add(seller)
            await session.flush()

            gate = await evaluate_seller_publish_gate(session, seller_user_id=seller.id)

    assert gate.allowed is True
    assert gate.risk_level == "LOW"
    assert gate.risk_score == 0
    assert gate.block_message is None


@pytest.mark.asyncio
async def test_publish_gate_blocks_high_risk_without_guarantor(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=93911, username="high_risk_seller")
            reporter = User(tg_user_id=93912, username="reporter")
            session.add_all([seller, reporter])
            await session.flush()

            auction = _make_auction(seller_user_id=seller.id, description="risk-lot")
            session.add(auction)
            await session.flush()

            session.add_all(
                [
                    Complaint(
                        auction_id=auction.id,
                        reporter_user_id=reporter.id,
                        target_user_id=seller.id,
                        reason=f"complaint-{idx}",
                        status="OPEN",
                    )
                    for idx in range(3)
                ]
            )
            session.add(
                FraudSignal(
                    auction_id=auction.id,
                    user_id=seller.id,
                    bid_id=None,
                    score=85,
                    reasons={"rules": [{"code": "TEST", "detail": "risk", "score": 85}]},
                    status="OPEN",
                )
            )
            await session.flush()

            gate = await evaluate_seller_publish_gate(session, seller_user_id=seller.id)

    assert gate.allowed is False
    assert gate.risk_level == "HIGH"
    assert gate.risk_score >= 70
    assert gate.block_message is not None
    assert "/guarant" in gate.block_message


@pytest.mark.asyncio
async def test_publish_gate_allows_high_risk_with_recent_assigned_guarantor(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=93921, username="high_risk_allowed")
            reporter = User(tg_user_id=93922, username="reporter2")
            moderator = User(tg_user_id=93923, username="mod2")
            session.add_all([seller, reporter, moderator])
            await session.flush()

            auction = _make_auction(seller_user_id=seller.id, description="risk-lot-ok")
            session.add(auction)
            await session.flush()

            session.add_all(
                [
                    Complaint(
                        auction_id=auction.id,
                        reporter_user_id=reporter.id,
                        target_user_id=seller.id,
                        reason=f"complaint-{idx}",
                        status="OPEN",
                    )
                    for idx in range(3)
                ]
            )
            session.add(
                FraudSignal(
                    auction_id=auction.id,
                    user_id=seller.id,
                    bid_id=None,
                    score=90,
                    reasons={"rules": [{"code": "TEST", "detail": "risk", "score": 90}]},
                    status="OPEN",
                )
            )
            now = datetime.now(UTC)
            session.add(
                GuarantorRequest(
                    status=GuarantorRequestStatus.ASSIGNED,
                    submitter_user_id=seller.id,
                    moderator_user_id=moderator.id,
                    details="assigned guarantor",
                    resolution_note="ok",
                    resolved_at=now,
                    updated_at=now,
                )
            )
            await session.flush()

            gate = await evaluate_seller_publish_gate(session, seller_user_id=seller.id)

    assert gate.allowed is True
    assert gate.risk_level == "HIGH"
    assert gate.block_message is None


@pytest.mark.asyncio
async def test_publish_gate_blocks_with_stale_assigned_guarantor(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=93931, username="high_risk_stale")
            reporter = User(tg_user_id=93932, username="reporter3")
            moderator = User(tg_user_id=93933, username="mod3")
            session.add_all([seller, reporter, moderator])
            await session.flush()

            auction = _make_auction(seller_user_id=seller.id, description="risk-lot-stale")
            session.add(auction)
            await session.flush()

            session.add_all(
                [
                    Complaint(
                        auction_id=auction.id,
                        reporter_user_id=reporter.id,
                        target_user_id=seller.id,
                        reason=f"complaint-{idx}",
                        status="OPEN",
                    )
                    for idx in range(3)
                ]
            )
            session.add(
                FraudSignal(
                    auction_id=auction.id,
                    user_id=seller.id,
                    bid_id=None,
                    score=88,
                    reasons={"rules": [{"code": "TEST", "detail": "risk", "score": 88}]},
                    status="OPEN",
                )
            )

            stale = datetime.now(UTC) - timedelta(days=90)
            session.add(
                GuarantorRequest(
                    status=GuarantorRequestStatus.ASSIGNED,
                    submitter_user_id=seller.id,
                    moderator_user_id=moderator.id,
                    details="stale assigned guarantor",
                    resolution_note="old",
                    resolved_at=stale,
                    updated_at=stale,
                )
            )
            await session.flush()

            gate = await evaluate_seller_publish_gate(session, seller_user_id=seller.id)

    assert gate.allowed is False
    assert gate.risk_level == "HIGH"
    assert gate.block_message is not None


@pytest.mark.asyncio
async def test_publish_gate_consumes_verified_user_state_safely(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=93941, username="verified_seller")
            reporter = User(tg_user_id=93942, username="reporter4")
            session.add_all([seller, reporter])
            await session.flush()

            auction = _make_auction(seller_user_id=seller.id, description="verified-lot")
            session.add(auction)
            await session.flush()

            session.add(
                Complaint(
                    auction_id=auction.id,
                    reporter_user_id=reporter.id,
                    target_user_id=seller.id,
                    reason="single complaint",
                    status="OPEN",
                )
            )
            session.add(
                TelegramUserVerification(
                    tg_user_id=seller.tg_user_id,
                    is_verified=True,
                    custom_description="trusted",
                )
            )
            await session.flush()

            gate = await evaluate_seller_publish_gate(session, seller_user_id=seller.id)

    assert gate.allowed is True
    assert gate.risk_level == "LOW"
    assert gate.risk_score == 5
