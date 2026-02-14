from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import FeedbackStatus, FeedbackType, IntegrationOutboxStatus, PointsEventType
from app.db.models import FeedbackItem, IntegrationOutbox, PointsLedgerEntry, User
from app.services.feedback_service import (
    approve_feedback,
    create_feedback,
    redeem_feedback_priority_boost,
    reject_feedback,
    take_feedback_in_review,
)
from app.services.outbox_service import OUTBOX_EVENT_FEEDBACK_APPROVED
from app.services.points_service import feedback_reward_dedupe_key


@pytest.mark.asyncio
async def test_feedback_service_full_transition(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93001, username="submitter")
            moderator = User(tg_user_id=93002, username="moderator")
            session.add_all([submitter, moderator])
            await session.flush()

            created = await create_feedback(
                session,
                submitter_user_id=submitter.id,
                feedback_type=FeedbackType.BUG,
                content="Кнопка выкупа ломается после обновления",
            )
            assert created.ok is True
            assert created.item is not None

            in_review = await take_feedback_in_review(
                session,
                feedback_id=created.item.id,
                moderator_user_id=moderator.id,
                note="проверяю",
            )
            assert in_review.ok is True
            assert in_review.item is not None
            assert in_review.item.status == FeedbackStatus.IN_REVIEW

            approved = await approve_feedback(
                session,
                feedback_id=created.item.id,
                moderator_user_id=moderator.id,
                note="берем в работу",
            )
            assert approved.ok is True
            assert approved.changed is True
            assert approved.item is not None
            assert approved.item.status == FeedbackStatus.APPROVED
            assert approved.item.reward_points == 30

            approved_again = await approve_feedback(
                session,
                feedback_id=created.item.id,
                moderator_user_id=moderator.id,
                note="дубликат",
            )
            assert approved_again.ok is True
            assert approved_again.changed is False

            rejected_after_approve = await reject_feedback(
                session,
                feedback_id=created.item.id,
                moderator_user_id=moderator.id,
                note="неактуально",
            )
            assert rejected_after_approve.ok is False

    async with session_factory() as session:
        row = await session.scalar(select(FeedbackItem).where(FeedbackItem.type == FeedbackType.BUG))
        outbox_row = await session.scalar(select(IntegrationOutbox).where(IntegrationOutbox.event_type == OUTBOX_EVENT_FEEDBACK_APPROVED))
        points_row = await session.scalar(select(PointsLedgerEntry).where(PointsLedgerEntry.dedupe_key == feedback_reward_dedupe_key(row.id if row else -1)))

    assert row is not None
    assert row.status == FeedbackStatus.APPROVED
    assert row.resolved_at is not None
    assert outbox_row is not None
    assert outbox_row.status == IntegrationOutboxStatus.PENDING
    assert outbox_row.payload.get("feedback_id") == row.id
    assert points_row is not None
    assert points_row.amount == 30
    assert points_row.event_type == PointsEventType.FEEDBACK_APPROVED
    assert points_row.user_id == row.submitter_user_id


@pytest.mark.asyncio
async def test_feedback_create_respects_type_cooldown(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "feedback_intake_cooldown_seconds", 3600)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93011, username="cooldown_submitter")
            session.add(submitter)
            await session.flush()

            first = await create_feedback(
                session,
                submitter_user_id=submitter.id,
                feedback_type=FeedbackType.SUGGESTION,
                content="Добавьте узбекский язык в настройки",
            )
            second = await create_feedback(
                session,
                submitter_user_id=submitter.id,
                feedback_type=FeedbackType.SUGGESTION,
                content="Еще одно предложение сразу",
            )

    assert first.ok is True
    assert second.ok is False
    assert "Слишком часто" in second.message


@pytest.mark.asyncio
async def test_feedback_priority_boost_spends_points_and_marks_item(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "feedback_priority_boost_cost_points", 25)
    monkeypatch.setattr(settings, "feedback_priority_boost_daily_limit", 2)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93021, username="boost_submitter")
            session.add(submitter)
            await session.flush()

            created = await create_feedback(
                session,
                submitter_user_id=submitter.id,
                feedback_type=FeedbackType.SUGGESTION,
                content="Добавьте буст за points",
            )
            assert created.ok is True
            assert created.item is not None

            session.add(
                PointsLedgerEntry(
                    user_id=submitter.id,
                    amount=40,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="seed:boost:points",
                    reason="seed",
                    payload=None,
                )
            )
            await session.flush()

            boosted = await redeem_feedback_priority_boost(
                session,
                feedback_id=created.item.id,
                submitter_user_id=submitter.id,
            )
            assert boosted.ok is True
            assert boosted.changed is True
            assert boosted.item is not None
            assert boosted.item.priority_boost_points_spent == 25
            assert boosted.item.priority_boosted_at is not None

    async with session_factory() as session:
        item = await session.scalar(select(FeedbackItem).where(FeedbackItem.submitter_user_id == submitter.id))
        spend_row = await session.scalar(
            select(PointsLedgerEntry).where(PointsLedgerEntry.event_type == PointsEventType.FEEDBACK_PRIORITY_BOOST)
        )

    assert item is not None
    assert item.priority_boost_points_spent == 25
    assert spend_row is not None
    assert spend_row.amount == -25
    assert spend_row.user_id == submitter.id


@pytest.mark.asyncio
async def test_feedback_priority_boost_daily_limit(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "feedback_priority_boost_cost_points", 10)
    monkeypatch.setattr(settings, "feedback_priority_boost_daily_limit", 1)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93031, username="boost_limit_submitter")
            session.add(submitter)
            await session.flush()

            feedback_a = await create_feedback(
                session,
                submitter_user_id=submitter.id,
                feedback_type=FeedbackType.BUG,
                content="bug one details",
            )
            feedback_b = await create_feedback(
                session,
                submitter_user_id=submitter.id,
                feedback_type=FeedbackType.SUGGESTION,
                content="suggest two details",
            )
            assert feedback_a.item is not None and feedback_b.item is not None

            session.add(
                PointsLedgerEntry(
                    user_id=submitter.id,
                    amount=100,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="seed:boost:limit",
                    reason="seed",
                    payload=None,
                )
            )
            await session.flush()

            first = await redeem_feedback_priority_boost(
                session,
                feedback_id=feedback_a.item.id,
                submitter_user_id=submitter.id,
            )
            second = await redeem_feedback_priority_boost(
                session,
                feedback_id=feedback_b.item.id,
                submitter_user_id=submitter.id,
            )

    assert first.ok is True
    assert second.ok is False
    assert "дневной лимит" in second.message.lower()


@pytest.mark.asyncio
async def test_feedback_priority_boost_disabled_by_policy(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "feedback_priority_boost_enabled", False)
    monkeypatch.setattr(settings, "feedback_priority_boost_cost_points", 10)
    monkeypatch.setattr(settings, "feedback_priority_boost_daily_limit", 1)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93041, username="boost_disabled_submitter")
            session.add(submitter)
            await session.flush()

            created = await create_feedback(
                session,
                submitter_user_id=submitter.id,
                feedback_type=FeedbackType.BUG,
                content="Проверка отключенного буста",
            )
            assert created.item is not None

            session.add(
                PointsLedgerEntry(
                    user_id=submitter.id,
                    amount=20,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="seed:feedback:boost:disabled",
                    reason="seed",
                    payload=None,
                )
            )
            await session.flush()

            result = await redeem_feedback_priority_boost(
                session,
                feedback_id=created.item.id,
                submitter_user_id=submitter.id,
            )

    assert result.ok is False
    assert "временно отключен" in result.message.lower()

    async with session_factory() as session:
        item = await session.scalar(select(FeedbackItem).where(FeedbackItem.submitter_user_id == submitter.id))
        spend_row = await session.scalar(
            select(PointsLedgerEntry).where(PointsLedgerEntry.event_type == PointsEventType.FEEDBACK_PRIORITY_BOOST)
        )

    assert item is not None
    assert item.priority_boosted_at is None
    assert item.priority_boost_points_spent == 0
    assert spend_row is None


@pytest.mark.asyncio
async def test_feedback_priority_boost_disabled_by_global_redemption_toggle(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "points_redemption_enabled", False)
    monkeypatch.setattr(settings, "feedback_priority_boost_enabled", True)
    monkeypatch.setattr(settings, "feedback_priority_boost_cost_points", 10)
    monkeypatch.setattr(settings, "feedback_priority_boost_daily_limit", 1)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93042, username="boost_global_disabled_submitter")
            session.add(submitter)
            await session.flush()

            created = await create_feedback(
                session,
                submitter_user_id=submitter.id,
                feedback_type=FeedbackType.BUG,
                content="Проверка глобального отключения редимпшенов",
            )
            assert created.item is not None

            session.add(
                PointsLedgerEntry(
                    user_id=submitter.id,
                    amount=20,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="seed:feedback:boost:global_disabled",
                    reason="seed",
                    payload=None,
                )
            )
            await session.flush()

            result = await redeem_feedback_priority_boost(
                session,
                feedback_id=created.item.id,
                submitter_user_id=submitter.id,
            )

    assert result.ok is False
    assert "редимпшены points временно отключены" in result.message.lower()


@pytest.mark.asyncio
async def test_feedback_priority_boost_utility_cooldown(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "feedback_priority_boost_enabled", True)
    monkeypatch.setattr(settings, "feedback_priority_boost_cost_points", 10)
    monkeypatch.setattr(settings, "feedback_priority_boost_daily_limit", 3)
    monkeypatch.setattr(settings, "feedback_priority_boost_cooldown_seconds", 3600)
    monkeypatch.setattr(settings, "points_redemption_cooldown_seconds", 0)
    monkeypatch.setattr(settings, "feedback_intake_cooldown_seconds", 0)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93051, username="feedback_utility_cooldown")
            session.add(submitter)
            await session.flush()

            feedback_a = await create_feedback(
                session,
                submitter_user_id=submitter.id,
                feedback_type=FeedbackType.BUG,
                content="Feedback A for cooldown",
            )
            feedback_b = await create_feedback(
                session,
                submitter_user_id=submitter.id,
                feedback_type=FeedbackType.SUGGESTION,
                content="Feedback B for cooldown",
            )
            assert feedback_a.item is not None and feedback_b.item is not None

            session.add(
                PointsLedgerEntry(
                    user_id=submitter.id,
                    amount=50,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="seed:feedback:utility:cooldown",
                    reason="seed",
                    payload=None,
                )
            )
            await session.flush()

            first = await redeem_feedback_priority_boost(
                session,
                feedback_id=feedback_a.item.id,
                submitter_user_id=submitter.id,
            )
            second = await redeem_feedback_priority_boost(
                session,
                feedback_id=feedback_b.item.id,
                submitter_user_id=submitter.id,
            )

    assert first.ok is True
    assert second.ok is False
    assert "повторный буст фидбека" in second.message.lower()
