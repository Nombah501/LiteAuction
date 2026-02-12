from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers.moderation import mod_report_action, mod_risk_action
from app.db.enums import AuctionStatus
from app.db.models import Auction, BlacklistEntry, Complaint, FraudSignal, User


class _DummyFromUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.username = f"user{user_id}"
        self.first_name = "Test"
        self.last_name = "User"


class _DummyCallback:
    def __init__(self, *, data: str, from_user_id: int) -> None:
        self.data = data
        self.from_user = _DummyFromUser(from_user_id)
        self.message = None
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, *, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


class _DummyBot:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent_messages.append((chat_id, text))


@pytest.mark.asyncio
async def test_modrep_freeze_updates_db_and_refresh(monkeypatch, integration_engine) -> None:
    from app.config import settings

    actor_tg_user_id = 70001
    monkeypatch.setattr(settings, "admin_user_ids", str(actor_tg_user_id))
    monkeypatch.setattr(settings, "admin_operator_user_ids", "")

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", session_factory)

    refresh_calls: list[uuid.UUID] = []

    async def fake_refresh(_bot, auction_id: uuid.UUID) -> None:
        refresh_calls.append(auction_id)

    monkeypatch.setattr("app.bot.handlers.moderation.refresh_auction_posts", fake_refresh)

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=70101, username="seller")
            reporter = User(tg_user_id=70102, username="reporter")
            session.add_all([seller, reporter])
            await session.flush()

            auction = Auction(
                seller_user_id=seller.id,
                description="lot",
                photo_file_id="photo",
                start_price=10,
                buyout_price=None,
                min_step=1,
                duration_hours=24,
                status=AuctionStatus.ACTIVE,
            )
            session.add(auction)
            await session.flush()

            complaint = Complaint(
                auction_id=auction.id,
                reporter_user_id=reporter.id,
                reason="fraud suspicion",
                status="OPEN",
            )
            session.add(complaint)
            await session.flush()
            complaint_id = complaint.id
            auction_id = auction.id

    callback = _DummyCallback(data=f"modrep:freeze:{complaint_id}", from_user_id=actor_tg_user_id)
    bot = _DummyBot()

    await mod_report_action(callback, bot)

    async with session_factory() as session:
        complaint_row = await session.scalar(select(Complaint).where(Complaint.id == complaint_id))
        auction_row = await session.scalar(select(Auction).where(Auction.id == auction_id))

    assert complaint_row is not None
    assert auction_row is not None
    assert complaint_row.status == "RESOLVED"
    assert complaint_row.resolution_note == "Заморозка аукциона"
    assert auction_row.status == AuctionStatus.FROZEN
    assert refresh_calls == [auction_id]
    assert callback.answers[-1][0] == "Аукцион заморожен"


@pytest.mark.asyncio
async def test_modrep_ban_top_denied_for_operator_without_scope(monkeypatch, integration_engine) -> None:
    from app.config import settings

    owner_tg_user_id = 71001
    operator_tg_user_id = 71002
    monkeypatch.setattr(settings, "admin_user_ids", f"{owner_tg_user_id},{operator_tg_user_id}")
    monkeypatch.setattr(settings, "admin_operator_user_ids", str(operator_tg_user_id))

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", session_factory)

    refresh_calls: list[uuid.UUID] = []

    async def fake_refresh(_bot, auction_id: uuid.UUID) -> None:
        refresh_calls.append(auction_id)

    monkeypatch.setattr("app.bot.handlers.moderation.refresh_auction_posts", fake_refresh)

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=71101, username="seller")
            reporter = User(tg_user_id=71102, username="reporter")
            suspect = User(tg_user_id=71103, username="suspect")
            session.add_all([seller, reporter, suspect])
            await session.flush()

            auction = Auction(
                seller_user_id=seller.id,
                description="lot",
                photo_file_id="photo",
                start_price=10,
                buyout_price=None,
                min_step=1,
                duration_hours=24,
                status=AuctionStatus.ACTIVE,
            )
            session.add(auction)
            await session.flush()

            complaint = Complaint(
                auction_id=auction.id,
                reporter_user_id=reporter.id,
                target_user_id=suspect.id,
                reason="ban this user",
                status="OPEN",
            )
            session.add(complaint)
            await session.flush()
            complaint_id = complaint.id
            suspect_user_id = suspect.id

    callback = _DummyCallback(data=f"modrep:ban_top:{complaint_id}", from_user_id=operator_tg_user_id)
    bot = _DummyBot()

    await mod_report_action(callback, bot)

    async with session_factory() as session:
        complaint_row = await session.scalar(select(Complaint).where(Complaint.id == complaint_id))
        blacklist = await session.scalar(select(BlacklistEntry).where(BlacklistEntry.user_id == suspect_user_id))

    assert complaint_row is not None
    assert complaint_row.status == "OPEN"
    assert blacklist is None
    assert refresh_calls == []
    assert callback.answers
    assert callback.answers[-1][1] is True
    assert "Недостаточно прав" in (callback.answers[-1][0] or "")


@pytest.mark.asyncio
async def test_modrisk_ban_updates_db_refresh_and_notifies(monkeypatch, integration_engine) -> None:
    from app.config import settings

    actor_tg_user_id = 72001
    monkeypatch.setattr(settings, "admin_user_ids", str(actor_tg_user_id))
    monkeypatch.setattr(settings, "admin_operator_user_ids", "")

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", session_factory)

    refresh_calls: list[uuid.UUID] = []

    async def fake_refresh(_bot, auction_id: uuid.UUID) -> None:
        refresh_calls.append(auction_id)

    monkeypatch.setattr("app.bot.handlers.moderation.refresh_auction_posts", fake_refresh)

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=72101, username="seller")
            suspect = User(tg_user_id=72102, username="suspect")
            session.add_all([seller, suspect])
            await session.flush()

            auction = Auction(
                seller_user_id=seller.id,
                description="lot",
                photo_file_id="photo",
                start_price=10,
                buyout_price=None,
                min_step=1,
                duration_hours=24,
                status=AuctionStatus.ACTIVE,
            )
            session.add(auction)
            await session.flush()

            signal = FraudSignal(
                auction_id=auction.id,
                user_id=suspect.id,
                bid_id=None,
                score=80,
                reasons={"rules": [{"code": "TEST", "detail": "risk", "score": 80}]},
                status="OPEN",
            )
            session.add(signal)
            await session.flush()
            signal_id = signal.id
            auction_id = auction.id
            suspect_tg_user_id = suspect.tg_user_id
            suspect_user_id = suspect.id

    callback = _DummyCallback(data=f"modrisk:ban:{signal_id}", from_user_id=actor_tg_user_id)
    bot = _DummyBot()

    await mod_risk_action(callback, bot)

    async with session_factory() as session:
        signal_row = await session.scalar(select(FraudSignal).where(FraudSignal.id == signal_id))
        blacklist = await session.scalar(select(BlacklistEntry).where(BlacklistEntry.user_id == suspect_user_id))

    assert signal_row is not None
    assert signal_row.status == "CONFIRMED"
    assert signal_row.resolution_note == "Пользователь заблокирован"
    assert blacklist is not None
    assert blacklist.is_active is True
    assert refresh_calls == [auction_id]
    assert bot.sent_messages == [
        (suspect_tg_user_id, f"Ваш аккаунт заблокирован модератором по фрод-сигналу #{signal_id}")
    ]
    assert callback.answers[-1][0] == "Пользователь заблокирован"
