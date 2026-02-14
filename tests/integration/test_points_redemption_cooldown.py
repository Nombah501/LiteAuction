from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import FeedbackType, PointsEventType
from app.db.models import PointsLedgerEntry, User
from app.services.feedback_service import create_feedback, redeem_feedback_priority_boost
from app.services.guarantor_service import create_guarantor_request, redeem_guarantor_priority_boost


@pytest.mark.asyncio
async def test_points_redemption_global_cooldown_blocks_second_boost(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "feedback_priority_boost_cost_points", 10)
    monkeypatch.setattr(settings, "feedback_priority_boost_daily_limit", 3)
    monkeypatch.setattr(settings, "guarantor_priority_boost_cost_points", 10)
    monkeypatch.setattr(settings, "guarantor_priority_boost_daily_limit", 3)
    monkeypatch.setattr(settings, "feedback_intake_cooldown_seconds", 0)
    monkeypatch.setattr(settings, "guarantor_intake_cooldown_seconds", 0)
    monkeypatch.setattr(settings, "points_redemption_cooldown_seconds", 3600)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93901, username="cooldown_redeemer")
            session.add(submitter)
            await session.flush()

            feedback = await create_feedback(
                session,
                submitter_user_id=submitter.id,
                feedback_type=FeedbackType.BUG,
                content="Проверка глобального кулдауна редимпшена",
            )
            guarantor = await create_guarantor_request(
                session,
                submitter_user_id=submitter.id,
                details="Проверка второго буста в пределах кулдауна",
            )
            assert feedback.item is not None and guarantor.item is not None

            session.add(
                PointsLedgerEntry(
                    user_id=submitter.id,
                    amount=100,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="seed:redemption:cooldown",
                    reason="seed",
                    payload=None,
                )
            )
            await session.flush()

            first = await redeem_feedback_priority_boost(
                session,
                feedback_id=feedback.item.id,
                submitter_user_id=submitter.id,
            )
            second = await redeem_guarantor_priority_boost(
                session,
                request_id=guarantor.item.id,
                submitter_user_id=submitter.id,
            )

    assert first.ok is True
    assert second.ok is False
    assert "следующий буст доступен через" in second.message.lower()


@pytest.mark.asyncio
async def test_points_redemption_cooldown_zero_allows_multiple_boosts(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "feedback_priority_boost_cost_points", 10)
    monkeypatch.setattr(settings, "feedback_priority_boost_daily_limit", 3)
    monkeypatch.setattr(settings, "guarantor_priority_boost_cost_points", 10)
    monkeypatch.setattr(settings, "guarantor_priority_boost_daily_limit", 3)
    monkeypatch.setattr(settings, "feedback_intake_cooldown_seconds", 0)
    monkeypatch.setattr(settings, "guarantor_intake_cooldown_seconds", 0)
    monkeypatch.setattr(settings, "points_redemption_cooldown_seconds", 0)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93902, username="cooldown_disabled")
            session.add(submitter)
            await session.flush()

            feedback = await create_feedback(
                session,
                submitter_user_id=submitter.id,
                feedback_type=FeedbackType.SUGGESTION,
                content="Кулдаун выключен",
            )
            guarantor = await create_guarantor_request(
                session,
                submitter_user_id=submitter.id,
                details="Кулдаун выключен для второго редима",
            )
            assert feedback.item is not None and guarantor.item is not None

            session.add(
                PointsLedgerEntry(
                    user_id=submitter.id,
                    amount=100,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="seed:redemption:no_cooldown",
                    reason="seed",
                    payload=None,
                )
            )
            await session.flush()

            first = await redeem_feedback_priority_boost(
                session,
                feedback_id=feedback.item.id,
                submitter_user_id=submitter.id,
            )
            second = await redeem_guarantor_priority_boost(
                session,
                request_id=guarantor.item.id,
                submitter_user_id=submitter.id,
            )

    assert first.ok is True
    assert second.ok is True
