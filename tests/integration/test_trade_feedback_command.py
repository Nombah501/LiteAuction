from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers.trade_feedback import command_tradefeedback
from app.db.enums import AuctionStatus
from app.db.models import Auction, TradeFeedback, User


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


@pytest.mark.asyncio
async def test_tradefeedback_command_creates_feedback(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.trade_feedback.SessionFactory", session_factory)

    auction_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=99601, username="seller")
            winner = User(tg_user_id=99602, username="winner")
            session.add_all([seller, winner])
            await session.flush()

            session.add(
                Auction(
                    id=auction_id,
                    seller_user_id=seller.id,
                    winner_user_id=winner.id,
                    description="ended auction",
                    photo_file_id="photo",
                    start_price=100,
                    buyout_price=None,
                    min_step=5,
                    duration_hours=24,
                    status=AuctionStatus.ENDED,
                )
            )

    message = _DummyMessage(from_user_id=99601, text=f"/tradefeedback {auction_id} 5 Отличная сделка")
    await command_tradefeedback(message)

    assert message.answers
    assert "Отзыв сохранен" in message.answers[-1]

    async with session_factory() as session:
        rows = (await session.execute(select(TradeFeedback))).scalars().all()

    assert len(rows) == 1
    assert rows[0].rating == 5
    assert rows[0].comment == "Отличная сделка"
    assert rows[0].status == "VISIBLE"


@pytest.mark.asyncio
async def test_tradefeedback_command_rejects_non_participant(monkeypatch, integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.trade_feedback.SessionFactory", session_factory)

    auction_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=99611, username="seller2")
            winner = User(tg_user_id=99612, username="winner2")
            outsider = User(tg_user_id=99613, username="outsider")
            session.add_all([seller, winner, outsider])
            await session.flush()

            session.add(
                Auction(
                    id=auction_id,
                    seller_user_id=seller.id,
                    winner_user_id=winner.id,
                    description="ended auction",
                    photo_file_id="photo",
                    start_price=100,
                    buyout_price=None,
                    min_step=5,
                    duration_hours=24,
                    status=AuctionStatus.BOUGHT_OUT,
                )
            )

    message = _DummyMessage(from_user_id=99613, text=f"/tradefeedback {auction_id} 4")
    await command_tradefeedback(message)

    assert message.answers
    assert "только продавец и победитель" in message.answers[-1]

    async with session_factory() as session:
        rows = (await session.execute(select(TradeFeedback))).scalars().all()

    assert rows == []
