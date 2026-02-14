from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers.points import command_points
from app.db.enums import FeedbackType, PointsEventType
from app.services.feedback_service import create_feedback
from app.services.guarantor_service import create_guarantor_request
from app.services.points_service import grant_points


class _DummyFromUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.username = f"user{user_id}"
        self.first_name = "Test"
        self.last_name = "User"


class _DummyMessage:
    def __init__(self, from_user_id: int, text: str = "/points") -> None:
        self.from_user = _DummyFromUser(from_user_id)
        self.text = text
        self.answers: list[str] = []

    async def answer(self, text: str, **_kwargs) -> None:
        self.answers.append(text)


@pytest.mark.asyncio
async def test_points_command_shows_balance_and_history(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.points.SessionFactory", session_factory)

    message = _DummyMessage(from_user_id=93601)

    async with session_factory() as session:
        async with session.begin():
            from app.services.user_service import upsert_user

            user = await upsert_user(session, message.from_user, mark_private_started=True)
            await grant_points(
                session,
                user_id=user.id,
                amount=30,
                event_type=PointsEventType.FEEDBACK_APPROVED,
                dedupe_key="feedback:701:reward",
                reason="Награда за одобренный фидбек",
            )
            await grant_points(
                session,
                user_id=user.id,
                amount=-5,
                event_type=PointsEventType.MANUAL_ADJUSTMENT,
                dedupe_key="manual:701:penalty",
                reason="Корректировка",
            )

    await command_points(message)

    assert message.answers
    reply_text = message.answers[-1]
    assert "Ваш баланс: 25 points" in reply_text
    assert "Всего начислено: +30" in reply_text
    assert "Всего списано: -5" in reply_text
    assert "Буст фидбека: /boostfeedback <feedback_id>" in reply_text
    assert "Лимит фидбек-бустов сегодня:" in reply_text
    assert "Буст гаранта: /boostguarant <request_id>" in reply_text
    assert "Лимит бустов гаранта сегодня:" in reply_text
    assert "Глобальный кулдаун между бустами:" in reply_text
    assert "Последние операции (до 5):" in reply_text
    assert "-5" in reply_text


@pytest.mark.asyncio
async def test_points_command_shows_boost_usage_status(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.points.SessionFactory", session_factory)
    monkeypatch.setattr(settings, "feedback_priority_boost_cost_points", 20)
    monkeypatch.setattr(settings, "feedback_priority_boost_daily_limit", 2)
    monkeypatch.setattr(settings, "guarantor_priority_boost_cost_points", 40)
    monkeypatch.setattr(settings, "guarantor_priority_boost_daily_limit", 1)

    message = _DummyMessage(from_user_id=93631)

    async with session_factory() as session:
        async with session.begin():
            from app.services.user_service import upsert_user

            user = await upsert_user(session, message.from_user, mark_private_started=True)
            created = await create_feedback(
                session,
                submitter_user_id=user.id,
                feedback_type=FeedbackType.SUGGESTION,
                content="Покажите статус буста в /points",
            )
            assert created.item is not None
            created.item.priority_boost_points_spent = 20
            created.item.priority_boosted_at = datetime.now(UTC)
            guarantor = await create_guarantor_request(
                session,
                submitter_user_id=user.id,
                details="Нужен гарант для срочной сделки",
            )
            assert guarantor.item is not None
            guarantor.item.priority_boost_points_spent = 40
            guarantor.item.priority_boosted_at = datetime.now(UTC)
            await session.flush()

    await command_points(message)

    assert message.answers
    reply_text = message.answers[-1]
    assert "Буст фидбека: /boostfeedback <feedback_id> (стоимость: 20 points)" in reply_text
    assert "Лимит фидбек-бустов сегодня: 1/2 (осталось 1)" in reply_text
    assert "Буст гаранта: /boostguarant <request_id> (стоимость: 40 points)" in reply_text
    assert "Лимит бустов гаранта сегодня: 1/1 (осталось 0)" in reply_text


@pytest.mark.asyncio
async def test_points_command_supports_custom_limit(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.points.SessionFactory", session_factory)

    message = _DummyMessage(from_user_id=93611, text="/points 1")

    async with session_factory() as session:
        async with session.begin():
            from app.services.user_service import upsert_user

            user = await upsert_user(session, message.from_user, mark_private_started=True)
            await grant_points(
                session,
                user_id=user.id,
                amount=10,
                event_type=PointsEventType.MANUAL_ADJUSTMENT,
                dedupe_key="manual:711:1",
                reason="seed",
            )
            await grant_points(
                session,
                user_id=user.id,
                amount=20,
                event_type=PointsEventType.FEEDBACK_APPROVED,
                dedupe_key="feedback:711:reward",
                reason="seed",
            )

    await command_points(message)

    assert message.answers
    reply_text = message.answers[-1]
    assert "Последние операции (до 1):" in reply_text
    assert reply_text.count("\n-") == 1


@pytest.mark.asyncio
async def test_points_command_rejects_invalid_limit(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.points.SessionFactory", session_factory)

    message = _DummyMessage(from_user_id=93621, text="/points 0")
    await command_points(message)

    assert message.answers
    assert "Формат: /points [1..20]" in message.answers[-1]
