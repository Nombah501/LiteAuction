from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import FeedbackStatus, FeedbackType, IntegrationOutboxStatus, PointsEventType
from app.db.models import FeedbackItem, IntegrationOutbox, PointsLedgerEntry, User
from app.services.feedback_service import (
    approve_feedback,
    create_feedback,
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
