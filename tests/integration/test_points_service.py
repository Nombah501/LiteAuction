from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import PointsEventType
from app.db.models import PointsLedgerEntry, User
from app.services.points_service import (
    get_points_redemptions_spent_today,
    count_user_points_entries,
    get_points_redemptions_used_today,
    get_user_points_balance,
    get_user_points_summary,
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
        summary = await get_user_points_summary(session, user_id=user.id)
        recent = await list_user_points_entries(session, user_id=user.id, limit=2)

    assert balance == 25
    assert summary.balance == 25
    assert summary.total_earned == 30
    assert summary.total_spent == 5
    assert summary.operations_count == 3
    assert len(recent) == 2
    assert recent[0].dedupe_key == "manual:611:bonus"
    assert recent[1].dedupe_key == "manual:611:decay"


@pytest.mark.asyncio
async def test_points_list_and_count_support_filter_and_offset(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            user = User(tg_user_id=93521, username="points_filter")
            session.add(user)
            await session.flush()

            await grant_points(
                session,
                user_id=user.id,
                amount=20,
                event_type=PointsEventType.FEEDBACK_APPROVED,
                dedupe_key="feedback:621:reward",
                reason="Награда",
            )
            await grant_points(
                session,
                user_id=user.id,
                amount=5,
                event_type=PointsEventType.MANUAL_ADJUSTMENT,
                dedupe_key="manual:621:plus",
                reason="Бонус",
            )
            await grant_points(
                session,
                user_id=user.id,
                amount=-2,
                event_type=PointsEventType.MANUAL_ADJUSTMENT,
                dedupe_key="manual:621:minus",
                reason="Корректировка",
            )

    async with session_factory() as session:
        all_count = await count_user_points_entries(session, user_id=user.id)
        manual_count = await count_user_points_entries(
            session,
            user_id=user.id,
            event_type=PointsEventType.MANUAL_ADJUSTMENT,
        )
        manual_rows = await list_user_points_entries(
            session,
            user_id=user.id,
            limit=10,
            event_type=PointsEventType.MANUAL_ADJUSTMENT,
        )
        paged_rows = await list_user_points_entries(session, user_id=user.id, limit=1, offset=1)

    assert all_count == 3
    assert manual_count == 2
    assert len(manual_rows) == 2
    assert all(row.event_type == PointsEventType.MANUAL_ADJUSTMENT for row in manual_rows)
    assert len(paged_rows) == 1


@pytest.mark.asyncio
async def test_points_redemptions_used_today_counts_boost_spends_only(integration_engine) -> None:
    from datetime import UTC, datetime, timedelta

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            user = User(tg_user_id=93531, username="points_daily_limit")
            session.add(user)
            await session.flush()

            now = datetime.now(UTC)
            yesterday = now - timedelta(days=1)

            session.add(
                PointsLedgerEntry(
                    user_id=user.id,
                    amount=-10,
                    event_type=PointsEventType.FEEDBACK_PRIORITY_BOOST,
                    dedupe_key="daily:boost:today:1",
                    reason="seed",
                    payload=None,
                    created_at=now,
                )
            )
            session.add(
                PointsLedgerEntry(
                    user_id=user.id,
                    amount=-12,
                    event_type=PointsEventType.GUARANTOR_PRIORITY_BOOST,
                    dedupe_key="daily:boost:today:2",
                    reason="seed",
                    payload=None,
                    created_at=now,
                )
            )
            session.add(
                PointsLedgerEntry(
                    user_id=user.id,
                    amount=-5,
                    event_type=PointsEventType.FEEDBACK_PRIORITY_BOOST,
                    dedupe_key="daily:boost:yesterday",
                    reason="seed",
                    payload=None,
                    created_at=yesterday,
                )
            )
            session.add(
                PointsLedgerEntry(
                    user_id=user.id,
                    amount=20,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="daily:reward:today",
                    reason="seed",
                    payload=None,
                    created_at=now,
                )
            )

    async with session_factory() as session:
        used_today = await get_points_redemptions_used_today(session, user_id=user.id)

    assert used_today == 2


@pytest.mark.asyncio
async def test_points_redemptions_spent_today_sums_boost_spends_only(integration_engine) -> None:
    from datetime import UTC, datetime, timedelta

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            user = User(tg_user_id=93541, username="points_daily_spend")
            session.add(user)
            await session.flush()

            now = datetime.now(UTC)
            yesterday = now - timedelta(days=1)

            session.add(
                PointsLedgerEntry(
                    user_id=user.id,
                    amount=-10,
                    event_type=PointsEventType.FEEDBACK_PRIORITY_BOOST,
                    dedupe_key="daily:spend:today:1",
                    reason="seed",
                    payload=None,
                    created_at=now,
                )
            )
            session.add(
                PointsLedgerEntry(
                    user_id=user.id,
                    amount=-12,
                    event_type=PointsEventType.GUARANTOR_PRIORITY_BOOST,
                    dedupe_key="daily:spend:today:2",
                    reason="seed",
                    payload=None,
                    created_at=now,
                )
            )
            session.add(
                PointsLedgerEntry(
                    user_id=user.id,
                    amount=-5,
                    event_type=PointsEventType.APPEAL_PRIORITY_BOOST,
                    dedupe_key="daily:spend:yesterday",
                    reason="seed",
                    payload=None,
                    created_at=yesterday,
                )
            )
            session.add(
                PointsLedgerEntry(
                    user_id=user.id,
                    amount=-9,
                    event_type=PointsEventType.MANUAL_ADJUSTMENT,
                    dedupe_key="daily:spend:manual",
                    reason="seed",
                    payload=None,
                    created_at=now,
                )
            )

    async with session_factory() as session:
        spent_today = await get_points_redemptions_spent_today(session, user_id=user.id)

    assert spent_today == 22
