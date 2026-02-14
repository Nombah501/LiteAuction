from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import GuarantorRequestStatus, PointsEventType
from app.db.models import GuarantorRequest, PointsLedgerEntry, User
from app.services.guarantor_service import (
    assign_guarantor_request,
    create_guarantor_request,
    redeem_guarantor_priority_boost,
    reject_guarantor_request,
)


@pytest.mark.asyncio
async def test_guarantor_service_full_transition(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93301, username="guarant_submitter")
            moderator = User(tg_user_id=93302, username="guarant_mod")
            session.add_all([submitter, moderator])
            await session.flush()

            created = await create_guarantor_request(
                session,
                submitter_user_id=submitter.id,
                details="Нужен гарант для сделки по лоту #123, сумма 12000",
            )
            assert created.ok is True
            assert created.item is not None

            assigned = await assign_guarantor_request(
                session,
                request_id=created.item.id,
                moderator_user_id=moderator.id,
                note="Беру как гарант",
            )
            assert assigned.ok is True
            assert assigned.changed is True
            assert assigned.item is not None
            assert assigned.item.status == GuarantorRequestStatus.ASSIGNED

            assigned_again = await assign_guarantor_request(
                session,
                request_id=created.item.id,
                moderator_user_id=moderator.id,
                note="дубликат",
            )
            assert assigned_again.ok is True
            assert assigned_again.changed is False

            rejected_after_assign = await reject_guarantor_request(
                session,
                request_id=created.item.id,
                moderator_user_id=moderator.id,
                note="не нужно",
            )
            assert rejected_after_assign.ok is False

    async with session_factory() as session:
        row = await session.scalar(select(GuarantorRequest).where(GuarantorRequest.submitter_user_id == submitter.id))

    assert row is not None
    assert row.status == GuarantorRequestStatus.ASSIGNED
    assert row.resolved_at is not None


@pytest.mark.asyncio
async def test_guarantor_create_respects_cooldown(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "guarantor_intake_cooldown_seconds", 3600)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93311, username="guarant_cooldown")
            session.add(submitter)
            await session.flush()

            first = await create_guarantor_request(
                session,
                submitter_user_id=submitter.id,
                details="Первый запрос на гаранта для сделки",
            )
            second = await create_guarantor_request(
                session,
                submitter_user_id=submitter.id,
                details="Повторный запрос сразу",
            )

    assert first.ok is True
    assert second.ok is False
    assert "Слишком часто" in second.message


@pytest.mark.asyncio
async def test_guarantor_priority_boost_spends_points_and_marks_item(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "guarantor_priority_boost_cost_points", 40)
    monkeypatch.setattr(settings, "guarantor_priority_boost_daily_limit", 1)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93321, username="guarant_boost_submitter")
            session.add(submitter)
            await session.flush()

            created = await create_guarantor_request(
                session,
                submitter_user_id=submitter.id,
                details="Нужен гарант для сделки с предоплатой",
            )
            assert created.ok is True
            assert created.item is not None

            session.add(
                PointsLedgerEntry(
                    user_id=submitter.id,
                    amount=60,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="seed:guarant:boost:points",
                    reason="seed",
                    payload=None,
                )
            )
            await session.flush()

            boosted = await redeem_guarantor_priority_boost(
                session,
                request_id=created.item.id,
                submitter_user_id=submitter.id,
            )
            assert boosted.ok is True
            assert boosted.changed is True
            assert boosted.item is not None
            assert boosted.item.priority_boost_points_spent == 40
            assert boosted.item.priority_boosted_at is not None

    async with session_factory() as session:
        item = await session.scalar(select(GuarantorRequest).where(GuarantorRequest.submitter_user_id == submitter.id))
        spend_row = await session.scalar(
            select(PointsLedgerEntry).where(PointsLedgerEntry.event_type == PointsEventType.GUARANTOR_PRIORITY_BOOST)
        )

    assert item is not None
    assert item.priority_boost_points_spent == 40
    assert spend_row is not None
    assert spend_row.amount == -40
    assert spend_row.user_id == submitter.id


@pytest.mark.asyncio
async def test_guarantor_priority_boost_daily_limit(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "guarantor_priority_boost_cost_points", 10)
    monkeypatch.setattr(settings, "guarantor_priority_boost_daily_limit", 1)
    monkeypatch.setattr(settings, "guarantor_intake_cooldown_seconds", 0)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93331, username="guarant_boost_limit")
            session.add(submitter)
            await session.flush()

            request_a = await create_guarantor_request(
                session,
                submitter_user_id=submitter.id,
                details="Запрос гаранта A",
            )
            request_b = await create_guarantor_request(
                session,
                submitter_user_id=submitter.id,
                details="Запрос гаранта B",
            )
            assert request_a.item is not None and request_b.item is not None

            session.add(
                PointsLedgerEntry(
                    user_id=submitter.id,
                    amount=30,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="seed:guarant:boost:limit",
                    reason="seed",
                    payload=None,
                )
            )
            await session.flush()

            first = await redeem_guarantor_priority_boost(
                session,
                request_id=request_a.item.id,
                submitter_user_id=submitter.id,
            )
            second = await redeem_guarantor_priority_boost(
                session,
                request_id=request_b.item.id,
                submitter_user_id=submitter.id,
            )

    assert first.ok is True
    assert second.ok is False
    assert "дневной лимит" in second.message.lower()


@pytest.mark.asyncio
async def test_guarantor_priority_boost_disabled_by_policy(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "guarantor_priority_boost_enabled", False)
    monkeypatch.setattr(settings, "guarantor_priority_boost_cost_points", 10)
    monkeypatch.setattr(settings, "guarantor_priority_boost_daily_limit", 1)
    monkeypatch.setattr(settings, "guarantor_intake_cooldown_seconds", 0)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93341, username="guarant_boost_disabled")
            session.add(submitter)
            await session.flush()

            created = await create_guarantor_request(
                session,
                submitter_user_id=submitter.id,
                details="Проверка отключенного буста гаранта",
            )
            assert created.item is not None

            session.add(
                PointsLedgerEntry(
                    user_id=submitter.id,
                    amount=25,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="seed:guarant:boost:disabled",
                    reason="seed",
                    payload=None,
                )
            )
            await session.flush()

            result = await redeem_guarantor_priority_boost(
                session,
                request_id=created.item.id,
                submitter_user_id=submitter.id,
            )

    assert result.ok is False
    assert "временно отключен" in result.message.lower()

    async with session_factory() as session:
        item = await session.scalar(
            select(GuarantorRequest).where(GuarantorRequest.submitter_user_id == submitter.id)
        )
        spend_row = await session.scalar(
            select(PointsLedgerEntry).where(PointsLedgerEntry.event_type == PointsEventType.GUARANTOR_PRIORITY_BOOST)
        )

    assert item is not None
    assert item.priority_boosted_at is None
    assert item.priority_boost_points_spent == 0
    assert spend_row is None


@pytest.mark.asyncio
async def test_guarantor_priority_boost_disabled_by_global_redemption_toggle(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "points_redemption_enabled", False)
    monkeypatch.setattr(settings, "guarantor_priority_boost_enabled", True)
    monkeypatch.setattr(settings, "guarantor_priority_boost_cost_points", 10)
    monkeypatch.setattr(settings, "guarantor_priority_boost_daily_limit", 1)
    monkeypatch.setattr(settings, "guarantor_intake_cooldown_seconds", 0)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93342, username="guarant_global_disabled")
            session.add(submitter)
            await session.flush()

            created = await create_guarantor_request(
                session,
                submitter_user_id=submitter.id,
                details="Проверка глобального отключения редимпшенов",
            )
            assert created.item is not None

            session.add(
                PointsLedgerEntry(
                    user_id=submitter.id,
                    amount=25,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="seed:guarant:boost:global_disabled",
                    reason="seed",
                    payload=None,
                )
            )
            await session.flush()

            result = await redeem_guarantor_priority_boost(
                session,
                request_id=created.item.id,
                submitter_user_id=submitter.id,
            )

    assert result.ok is False
    assert "редимпшены points временно отключены" in result.message.lower()


@pytest.mark.asyncio
async def test_guarantor_priority_boost_requires_min_account_age(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "points_redemption_enabled", True)
    monkeypatch.setattr(settings, "points_redemption_min_account_age_seconds", 3600)
    monkeypatch.setattr(settings, "guarantor_priority_boost_enabled", True)
    monkeypatch.setattr(settings, "guarantor_priority_boost_cost_points", 10)
    monkeypatch.setattr(settings, "guarantor_priority_boost_daily_limit", 1)
    monkeypatch.setattr(settings, "guarantor_intake_cooldown_seconds", 0)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93343, username="guarant_min_account_age")
            session.add(submitter)
            await session.flush()

            created = await create_guarantor_request(
                session,
                submitter_user_id=submitter.id,
                details="Проверка минимального возраста аккаунта для буста гаранта",
            )
            assert created.item is not None

            session.add(
                PointsLedgerEntry(
                    user_id=submitter.id,
                    amount=25,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="seed:guarant:boost:min_account_age",
                    reason="seed",
                    payload=None,
                )
            )
            await session.flush()

            result = await redeem_guarantor_priority_boost(
                session,
                request_id=created.item.id,
                submitter_user_id=submitter.id,
            )

    assert result.ok is False
    assert "после регистрации" in result.message.lower()

    async with session_factory() as session:
        item = await session.scalar(
            select(GuarantorRequest).where(GuarantorRequest.submitter_user_id == submitter.id)
        )
        spend_row = await session.scalar(
            select(PointsLedgerEntry).where(PointsLedgerEntry.event_type == PointsEventType.GUARANTOR_PRIORITY_BOOST)
        )

    assert item is not None
    assert item.priority_boosted_at is None
    assert item.priority_boost_points_spent == 0
    assert spend_row is None


@pytest.mark.asyncio
async def test_guarantor_priority_boost_utility_cooldown(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "guarantor_priority_boost_enabled", True)
    monkeypatch.setattr(settings, "guarantor_priority_boost_cost_points", 10)
    monkeypatch.setattr(settings, "guarantor_priority_boost_daily_limit", 3)
    monkeypatch.setattr(settings, "guarantor_priority_boost_cooldown_seconds", 3600)
    monkeypatch.setattr(settings, "points_redemption_cooldown_seconds", 0)
    monkeypatch.setattr(settings, "guarantor_intake_cooldown_seconds", 0)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93351, username="guarant_utility_cooldown")
            session.add(submitter)
            await session.flush()

            request_a = await create_guarantor_request(
                session,
                submitter_user_id=submitter.id,
                details="Guarantor request A",
            )
            request_b = await create_guarantor_request(
                session,
                submitter_user_id=submitter.id,
                details="Guarantor request B",
            )
            assert request_a.item is not None and request_b.item is not None

            session.add(
                PointsLedgerEntry(
                    user_id=submitter.id,
                    amount=50,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="seed:guarant:utility:cooldown",
                    reason="seed",
                    payload=None,
                )
            )
            await session.flush()

            first = await redeem_guarantor_priority_boost(
                session,
                request_id=request_a.item.id,
                submitter_user_id=submitter.id,
            )
            second = await redeem_guarantor_priority_boost(
                session,
                request_id=request_b.item.id,
                submitter_user_id=submitter.id,
            )

    assert first.ok is True
    assert second.ok is False
    assert "повторный буст гаранта" in second.message.lower()
