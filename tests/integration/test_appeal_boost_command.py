from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers.start import command_boost_appeal
from app.db.enums import PointsEventType
from app.db.models import Appeal, PointsLedgerEntry, User
from app.services.appeal_service import create_appeal_from_ref


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
        self.sent_messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **_kwargs) -> None:
        self.sent_messages.append((chat_id, text))


@pytest.mark.asyncio
async def test_boostappeal_command_spends_points_and_notifies(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.start.SessionFactory", session_factory)
    monkeypatch.setattr(settings, "appeal_priority_boost_cost_points", 20)
    monkeypatch.setattr(settings, "appeal_priority_boost_daily_limit", 1)
    monkeypatch.setattr(settings, "points_redemption_cooldown_seconds", 0)

    async with session_factory() as session:
        async with session.begin():
            appellant = User(tg_user_id=88701, username="appeal_boost_cmd")
            session.add(appellant)
            await session.flush()

            appeal = await create_appeal_from_ref(
                session,
                appellant_user_id=appellant.id,
                appeal_ref="manual_boost_command",
            )

            session.add(
                PointsLedgerEntry(
                    user_id=appellant.id,
                    amount=35,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="seed:appeal:boost:command",
                    reason="seed",
                    payload=None,
                )
            )
            appeal_id = appeal.id

    message = _DummyMessage(from_user_id=88701, text=f"/boostappeal {appeal_id}")
    bot = _DummyBot()
    await command_boost_appeal(message, bot)

    assert message.answers
    assert "повышен" in message.answers[-1].lower()
    assert "уведомление" in message.answers[-1].lower()

    async with session_factory() as session:
        stored = await session.scalar(select(Appeal).where(Appeal.id == appeal_id))
        assert stored is not None
        spend = await session.scalar(
            select(PointsLedgerEntry)
            .where(PointsLedgerEntry.user_id == stored.appellant_user_id)
            .order_by(PointsLedgerEntry.id.desc())
            .limit(1)
        )

    assert stored.priority_boost_points_spent == 20
    assert spend is not None
    assert spend.event_type == PointsEventType.APPEAL_PRIORITY_BOOST
    assert spend.amount == -20
