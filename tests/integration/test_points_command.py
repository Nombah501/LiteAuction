from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers.points import command_points
from app.db.enums import PointsEventType
from app.services.points_service import grant_points


class _DummyFromUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.username = f"user{user_id}"
        self.first_name = "Test"
        self.last_name = "User"


class _DummyMessage:
    def __init__(self, from_user_id: int) -> None:
        self.from_user = _DummyFromUser(from_user_id)
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
    assert "Последние операции:" in reply_text
    assert "-5" in reply_text
