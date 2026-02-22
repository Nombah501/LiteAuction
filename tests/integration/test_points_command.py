from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers.points import command_points
from app.db.enums import FeedbackType, PointsEventType
from app.services.appeal_service import create_appeal_from_ref
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
async def test_points_command_shows_compact_balance_and_actions(monkeypatch, integration_engine) -> None:
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
    assert "Баланс: 25 points" in reply_text
    assert "Начислено/списано: +30 / -5" in reply_text
    assert "Быстрые действия:" in reply_text
    assert "- Фидбек: /boostfeedback <feedback_id>" in reply_text
    assert "- Гарант: /boostguarant <request_id>" in reply_text
    assert "- Апелляция: /boostappeal <appeal_id>" in reply_text
    assert "Подробный режим: /points detailed" in reply_text
    assert "Последние операции (до 5):" in reply_text
    assert "-5" in reply_text
    assert "Глобальный месячный лимит списания:" not in reply_text


@pytest.mark.asyncio
async def test_points_command_detailed_mode_shows_boost_usage_status(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.points.SessionFactory", session_factory)
    monkeypatch.setattr(settings, "feedback_priority_boost_cost_points", 20)
    monkeypatch.setattr(settings, "feedback_priority_boost_daily_limit", 2)
    monkeypatch.setattr(settings, "feedback_priority_boost_cooldown_seconds", 30)
    monkeypatch.setattr(settings, "guarantor_priority_boost_cost_points", 40)
    monkeypatch.setattr(settings, "guarantor_priority_boost_daily_limit", 1)
    monkeypatch.setattr(settings, "guarantor_priority_boost_cooldown_seconds", 60)
    monkeypatch.setattr(settings, "appeal_priority_boost_cost_points", 20)
    monkeypatch.setattr(settings, "appeal_priority_boost_daily_limit", 2)
    monkeypatch.setattr(settings, "appeal_priority_boost_cooldown_seconds", 90)

    message = _DummyMessage(from_user_id=93631, text="/points detailed")

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
            appeal = await create_appeal_from_ref(
                session,
                appellant_user_id=user.id,
                appeal_ref="manual_points_status",
            )
            appeal.priority_boost_points_spent = 20
            appeal.priority_boosted_at = datetime.now(UTC)
            await session.flush()

    await command_points(message)

    assert message.answers
    reply_text = message.answers[-1]
    assert "Буст фидбека: /boostfeedback <feedback_id> (стоимость: 20 points)" in reply_text
    assert "Лимит фидбек-бустов сегодня: 1/2 (осталось 1)" in reply_text
    assert "Кулдаун фидбек-буста: 30 сек" in reply_text
    assert "Буст гаранта: /boostguarant <request_id> (стоимость: 40 points)" in reply_text
    assert "Лимит бустов гаранта сегодня: 1/1 (осталось 0)" in reply_text
    assert "Кулдаун буста гаранта: 60 сек" in reply_text
    assert "Буст апелляции: /boostappeal <appeal_id> (стоимость: 20 points)" in reply_text
    assert "Лимит бустов апелляций сегодня: 1/2 (осталось 1)" in reply_text
    assert "Кулдаун буста апелляции: 90 сек" in reply_text


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
async def test_points_command_compact_mode_shows_actionable_blockers(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.points.SessionFactory", session_factory)
    monkeypatch.setattr(settings, "feedback_priority_boost_enabled", False)
    monkeypatch.setattr(settings, "guarantor_priority_boost_enabled", True)
    monkeypatch.setattr(settings, "appeal_priority_boost_enabled", True)
    monkeypatch.setattr(settings, "feedback_priority_boost_cooldown_seconds", 120)
    monkeypatch.setattr(settings, "guarantor_priority_boost_cooldown_seconds", 45)
    monkeypatch.setattr(settings, "appeal_priority_boost_cooldown_seconds", 15)
    monkeypatch.setattr(settings, "points_redemption_enabled", False)
    monkeypatch.setattr(settings, "points_redemption_daily_limit", 2)
    monkeypatch.setattr(settings, "points_redemption_weekly_limit", 3)
    monkeypatch.setattr(settings, "points_redemption_daily_spend_cap", 50)
    monkeypatch.setattr(settings, "points_redemption_weekly_spend_cap", 100)
    monkeypatch.setattr(settings, "points_redemption_monthly_spend_cap", 200)
    monkeypatch.setattr(settings, "points_redemption_min_balance", 15)
    monkeypatch.setattr(settings, "points_redemption_min_account_age_seconds", 3600)
    monkeypatch.setattr(settings, "points_redemption_min_earned_points", 20)
    monkeypatch.setattr(settings, "points_redemption_cooldown_seconds", 3600)

    message = _DummyMessage(from_user_id=93640)

    async with session_factory() as session:
        async with session.begin():
            from app.services.user_service import upsert_user

            user = await upsert_user(session, message.from_user, mark_private_started=True)
            await grant_points(
                session,
                user_id=user.id,
                amount=-10,
                event_type=PointsEventType.FEEDBACK_PRIORITY_BOOST,
                dedupe_key="seed:points:compact:blockers",
                reason="seed cooldown",
                payload=None,
            )

    await command_points(message)

    assert message.answers
    reply_text = message.answers[-1]
    assert "Сейчас блокирует:" in reply_text
    assert "Глобальные редимпшены временно отключены" in reply_text
    assert "До доступа по возрасту аккаунта:" in reply_text
    assert "До допуска по заработанным points:" in reply_text
    assert "До следующего буста:" in reply_text
    assert "Подробный режим: /points detailed" in reply_text
    assert "Глобальный месячный лимит списания:" not in reply_text


@pytest.mark.asyncio
async def test_points_command_detailed_mode_shows_boost_toggle_status_and_cooldown(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.points.SessionFactory", session_factory)
    monkeypatch.setattr(settings, "feedback_priority_boost_enabled", False)
    monkeypatch.setattr(settings, "guarantor_priority_boost_enabled", True)
    monkeypatch.setattr(settings, "appeal_priority_boost_enabled", True)
    monkeypatch.setattr(settings, "feedback_priority_boost_cooldown_seconds", 120)
    monkeypatch.setattr(settings, "guarantor_priority_boost_cooldown_seconds", 45)
    monkeypatch.setattr(settings, "appeal_priority_boost_cooldown_seconds", 15)
    monkeypatch.setattr(settings, "points_redemption_enabled", False)
    monkeypatch.setattr(settings, "points_redemption_daily_limit", 2)
    monkeypatch.setattr(settings, "points_redemption_weekly_limit", 3)
    monkeypatch.setattr(settings, "points_redemption_daily_spend_cap", 50)
    monkeypatch.setattr(settings, "points_redemption_weekly_spend_cap", 100)
    monkeypatch.setattr(settings, "points_redemption_monthly_spend_cap", 200)
    monkeypatch.setattr(settings, "points_redemption_min_balance", 15)
    monkeypatch.setattr(settings, "points_redemption_min_account_age_seconds", 3600)
    monkeypatch.setattr(settings, "points_redemption_min_earned_points", 20)
    monkeypatch.setattr(settings, "points_redemption_cooldown_seconds", 3600)

    message = _DummyMessage(from_user_id=93641, text="/points detailed")

    async with session_factory() as session:
        async with session.begin():
            from app.services.user_service import upsert_user

            user = await upsert_user(session, message.from_user, mark_private_started=True)
            await grant_points(
                session,
                user_id=user.id,
                amount=-10,
                event_type=PointsEventType.FEEDBACK_PRIORITY_BOOST,
                dedupe_key="seed:points:cooldown:status",
                reason="seed cooldown",
                payload=None,
            )

    await command_points(message)

    assert message.answers
    reply_text = message.answers[-1]
    assert "Статус фидбек-буста: временно отключен" in reply_text
    assert "Статус буста гаранта: доступен" in reply_text
    assert "Статус буста апелляции: доступен" in reply_text
    assert "Кулдаун фидбек-буста: 120 сек" in reply_text
    assert "Кулдаун буста гаранта: 45 сек" in reply_text
    assert "Кулдаун буста апелляции: 15 сек" in reply_text
    assert "Глобальный лимит бустов в день: 1/2 (осталось 1)" in reply_text
    assert "Глобальный лимит бустов в неделю: 1/3 (осталось 2)" in reply_text
    assert "Глобальный лимит списания на бусты: 10/50 points (осталось 40)" in reply_text
    assert "Глобальный недельный лимит списания: 10/100 points (осталось 90)" in reply_text
    assert "Глобальный месячный лимит списания: 10/200 points (осталось 190)" in reply_text
    assert "Глобальный статус редимпшенов: временно отключены" in reply_text
    assert "Минимальный остаток после буста: 15 points" in reply_text
    assert "Минимальный возраст аккаунта для буста: 3600 сек" in reply_text
    assert "Минимум заработанных points для буста: 20 points" in reply_text
    assert "До доступа к бустам по возрасту аккаунта:" in reply_text
    assert "До допуска по заработанным points:" in reply_text
    assert "До следующего буста:" in reply_text


@pytest.mark.asyncio
async def test_points_command_rejects_invalid_limit(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.points.SessionFactory", session_factory)

    message = _DummyMessage(from_user_id=93621, text="/points 0")
    await command_points(message)

    assert message.answers
    assert "Формат: /points [1..20]" in message.answers[-1]
    assert "/points detailed [1..20]" in message.answers[-1]


@pytest.mark.asyncio
async def test_points_command_supports_detailed_mode_with_custom_limit(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.points.SessionFactory", session_factory)

    message = _DummyMessage(from_user_id=93661, text="/points detailed 1")

    async with session_factory() as session:
        async with session.begin():
            from app.services.user_service import upsert_user

            user = await upsert_user(session, message.from_user, mark_private_started=True)
            await grant_points(
                session,
                user_id=user.id,
                amount=12,
                event_type=PointsEventType.FEEDBACK_APPROVED,
                dedupe_key="feedback:761:reward",
                reason="seed",
            )
            await grant_points(
                session,
                user_id=user.id,
                amount=-2,
                event_type=PointsEventType.MANUAL_ADJUSTMENT,
                dedupe_key="manual:761:spent",
                reason="seed",
            )

    await command_points(message)

    assert message.answers
    reply_text = message.answers[-1]
    assert "Ваш баланс: 10 points" in reply_text
    assert "Глобальный лимит бустов в день:" in reply_text
    assert "Последние операции (до 1):" in reply_text
    assert reply_text.count("\n-") == 1
