from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers.feedback import command_boost_feedback
from app.db.enums import FeedbackType, PointsEventType
from app.db.models import FeedbackItem, PointsLedgerEntry, User
from app.services.feedback_service import create_feedback


class _DummyFromUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.username = f"user{user_id}"
        self.first_name = "Test"
        self.last_name = "User"


class _DummyMessage:
    def __init__(self, from_user_id: int, text: str) -> None:
        self.from_user = _DummyFromUser(from_user_id)
        self.text = text
        self.answers: list[str] = []

    async def answer(self, text: str, **_kwargs) -> None:
        self.answers.append(text)


class _DummyBot:
    def __init__(self) -> None:
        self.edits: list[tuple[int, int, str]] = []

    async def edit_message_text(self, *, chat_id: int, message_id: int, text: str, **_kwargs) -> None:
        self.edits.append((chat_id, message_id, text))


@pytest.mark.asyncio
async def test_boostfeedback_command_spends_points_and_updates_queue(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.feedback.SessionFactory", session_factory)
    monkeypatch.setattr(settings, "feedback_priority_boost_cost_points", 20)
    monkeypatch.setattr(settings, "feedback_priority_boost_daily_limit", 2)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93101, username="boost_cmd_user")
            session.add(submitter)
            await session.flush()

            created = await create_feedback(
                session,
                submitter_user_id=submitter.id,
                feedback_type=FeedbackType.BUG,
                content="команда буста",
            )
            assert created.item is not None
            created.item.queue_chat_id = -100123
            created.item.queue_message_id = 55

            session.add(
                PointsLedgerEntry(
                    user_id=submitter.id,
                    amount=30,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="seed:boost:command",
                    reason="seed",
                    payload=None,
                )
            )
            feedback_id = created.item.id

    message = _DummyMessage(from_user_id=93101, text=f"/boostfeedback {feedback_id}")
    bot = _DummyBot()
    await command_boost_feedback(message, bot)

    assert message.answers
    assert "повышен" in message.answers[-1].lower()
    assert bot.edits

    async with session_factory() as session:
        item = await session.scalar(select(FeedbackItem).where(FeedbackItem.id == feedback_id))
        spend = await session.scalar(
            select(PointsLedgerEntry)
            .where(PointsLedgerEntry.user_id == item.submitter_user_id)
            .order_by(PointsLedgerEntry.id.desc())
            .limit(1)
        )

    assert item is not None
    assert item.priority_boost_points_spent == 20
    assert spend is not None
    assert spend.event_type == PointsEventType.FEEDBACK_PRIORITY_BOOST
    assert spend.amount == -20
