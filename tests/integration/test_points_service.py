from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import PointsEventType
from app.db.models import PointsLedgerEntry, User
from app.services.points_service import (
    get_user_points_balance,
    grant_points,
    list_user_points_entries,
)


@pytest.mark.asyncio
async def test_grant_points_is_idempotent_by_dedupe_key(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            user = User(tg_user_id=93501, username="points_user")
            session.add(user)
            await session.flush()

            first = await grant_points(
                session,
                user_id=user.id,
                amount=30,
                event_type=PointsEventType.FEEDBACK_APPROVED,
                dedupe_key="feedback:501:reward",
                reason="Награда за одобренный фидбек",
            )
            second = await grant_points(
                session,
                user_id=user.id,
                amount=30,
                event_type=PointsEventType.FEEDBACK_APPROVED,
                dedupe_key="feedback:501:reward",
                reason="Награда за одобренный фидбек",
            )

            assert first.changed is True
            assert second.changed is False

    async with session_factory() as session:
        entries = (
            await session.execute(
                select(PointsLedgerEntry)
                .where(PointsLedgerEntry.dedupe_key == "feedback:501:reward")
                .order_by(PointsLedgerEntry.id.asc())
            )
        ).scalars().all()
        balance = await get_user_points_balance(session, user_id=entries[0].user_id)

    assert len(entries) == 1
    assert entries[0].amount == 30
    assert entries[0].event_type == PointsEventType.FEEDBACK_APPROVED
    assert balance == 30


@pytest.mark.asyncio
async def test_points_balance_and_recent_entries(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            user = User(tg_user_id=93511, username="points_history")
            session.add(user)
            await session.flush()

            await grant_points(
                session,
                user_id=user.id,
                amount=20,
                event_type=PointsEventType.FEEDBACK_APPROVED,
                dedupe_key="feedback:611:reward",
                reason="Награда за одобренный фидбек",
            )
            await grant_points(
                session,
                user_id=user.id,
                amount=-5,
                event_type=PointsEventType.MANUAL_ADJUSTMENT,
                dedupe_key="manual:611:decay",
                reason="Корректировка",
            )
            await grant_points(
                session,
                user_id=user.id,
                amount=10,
                event_type=PointsEventType.MANUAL_ADJUSTMENT,
                dedupe_key="manual:611:bonus",
                reason="Бонус",
            )

    async with session_factory() as session:
        balance = await get_user_points_balance(session, user_id=user.id)
        recent = await list_user_points_entries(session, user_id=user.id, limit=2)

    assert balance == 25
    assert len(recent) == 2
    assert recent[0].dedupe_key == "manual:611:bonus"
    assert recent[1].dedupe_key == "manual:611:decay"
