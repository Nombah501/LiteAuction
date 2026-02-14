from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers.moderation import mod_points, mod_points_history, mod_stats
from app.db.enums import ModerationAction, PointsEventType
from app.db.models import ModerationLog, PointsLedgerEntry, User
from app.services.points_service import get_user_points_balance


class _DummyFromUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.username = f"user{user_id}"
        self.first_name = "Test"
        self.last_name = "User"


class _DummyChat:
    def __init__(self, chat_id: int) -> None:
        self.id = chat_id


class _DummyMessage:
    def __init__(self, *, text: str, from_user_id: int, chat_id: int = 5001, message_id: int = 10) -> None:
        self.text = text
        self.from_user = _DummyFromUser(from_user_id)
        self.chat = _DummyChat(chat_id)
        self.message_id = message_id
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


class _DummyBot:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **_kwargs) -> None:
        self.sent_messages.append((chat_id, text))


@pytest.mark.asyncio
async def test_modpoints_adjust_creates_ledger_and_audit(monkeypatch, integration_engine) -> None:
    from app.config import settings

    owner_tg_user_id = 93701
    target_tg_user_id = 93711
    monkeypatch.setattr(settings, "admin_user_ids", f"{owner_tg_user_id},93702")
    monkeypatch.setattr(settings, "admin_operator_user_ids", "93702")

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", session_factory)

    async with session_factory() as session:
        async with session.begin():
            target_user = User(tg_user_id=target_tg_user_id, username="points_target")
            session.add(target_user)

    message = _DummyMessage(
        text=f"/modpoints {target_tg_user_id} 15 ручная корректировка",
        from_user_id=owner_tg_user_id,
        chat_id=6001,
        message_id=42,
    )
    bot = _DummyBot()

    await mod_points(message, bot)

    assert message.answers
    assert "Изменение применено: +15 points" in message.answers[-1]
    assert bot.sent_messages
    assert bot.sent_messages[-1][0] == target_tg_user_id

    async with session_factory() as session:
        target = await session.scalar(select(User).where(User.tg_user_id == target_tg_user_id))
        assert target is not None
        balance = await get_user_points_balance(session, user_id=target.id)
        entry = await session.scalar(
            select(PointsLedgerEntry)
            .where(PointsLedgerEntry.user_id == target.id)
            .order_by(PointsLedgerEntry.id.desc())
        )
        log_row = await session.scalar(
            select(ModerationLog)
            .where(ModerationLog.action == ModerationAction.ADJUST_USER_POINTS)
            .order_by(ModerationLog.id.desc())
        )

    assert balance == 15
    assert entry is not None
    assert entry.amount == 15
    assert entry.event_type == PointsEventType.MANUAL_ADJUSTMENT
    assert log_row is not None
    assert log_row.payload is not None
    assert log_row.payload.get("amount") == 15


@pytest.mark.asyncio
async def test_modpoints_view_shows_balance(monkeypatch, integration_engine) -> None:
    from app.config import settings

    owner_tg_user_id = 93721
    target_tg_user_id = 93722
    monkeypatch.setattr(settings, "admin_user_ids", f"{owner_tg_user_id},93723")
    monkeypatch.setattr(settings, "admin_operator_user_ids", "93723")

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", session_factory)

    async with session_factory() as session:
        async with session.begin():
            target_user = User(tg_user_id=target_tg_user_id, username="points_target_view")
            session.add(target_user)
            await session.flush()

            session.add(
                PointsLedgerEntry(
                    user_id=target_user.id,
                    amount=20,
                    event_type=PointsEventType.MANUAL_ADJUSTMENT,
                    dedupe_key="manual:view:1",
                    reason="seed",
                    payload=None,
                )
            )
            session.add(
                PointsLedgerEntry(
                    user_id=target_user.id,
                    amount=-3,
                    event_type=PointsEventType.MANUAL_ADJUSTMENT,
                    dedupe_key="manual:view:2",
                    reason="seed2",
                    payload=None,
                )
            )

    message = _DummyMessage(text=f"/modpoints {target_tg_user_id} 1", from_user_id=owner_tg_user_id)
    bot = _DummyBot()

    await mod_points(message, bot)

    assert message.answers
    assert f"Баланс пользователя {target_tg_user_id}: 17 points" in message.answers[-1]
    assert "Всего начислено: +20" in message.answers[-1]
    assert "Всего списано: -3" in message.answers[-1]
    assert "Последние операции (до 1):" in message.answers[-1]
    assert message.answers[-1].count("\n-") == 1


@pytest.mark.asyncio
async def test_modpoints_view_rejects_invalid_limit(monkeypatch, integration_engine) -> None:
    from app.config import settings

    owner_tg_user_id = 93725
    target_tg_user_id = 93726
    monkeypatch.setattr(settings, "admin_user_ids", str(owner_tg_user_id))
    monkeypatch.setattr(settings, "admin_operator_user_ids", "")

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", session_factory)

    async with session_factory() as session:
        async with session.begin():
            target_user = User(tg_user_id=target_tg_user_id)
            session.add(target_user)

    message = _DummyMessage(text=f"/modpoints {target_tg_user_id} 0", from_user_id=owner_tg_user_id)
    bot = _DummyBot()

    await mod_points(message, bot)

    assert message.answers
    assert "Формат:" in message.answers[-1]


@pytest.mark.asyncio
async def test_modpoints_denied_for_operator_without_scope(monkeypatch, integration_engine) -> None:
    from app.config import settings

    owner_tg_user_id = 93731
    operator_tg_user_id = 93732
    target_tg_user_id = 93733
    monkeypatch.setattr(settings, "admin_user_ids", f"{owner_tg_user_id},{operator_tg_user_id}")
    monkeypatch.setattr(settings, "admin_operator_user_ids", str(operator_tg_user_id))

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", session_factory)

    async with session_factory() as session:
        async with session.begin():
            target_user = User(tg_user_id=target_tg_user_id)
            session.add(target_user)

    message = _DummyMessage(text=f"/modpoints {target_tg_user_id} 10 бонус", from_user_id=operator_tg_user_id)
    bot = _DummyBot()

    await mod_points(message, bot)

    assert message.answers
    assert "Недостаточно прав" in message.answers[-1]

    async with session_factory() as session:
        target = await session.scalar(select(User).where(User.tg_user_id == target_tg_user_id))
        assert target is not None
        balance = await get_user_points_balance(session, user_id=target.id)

    assert balance == 0


@pytest.mark.asyncio
async def test_modpoints_history_supports_filter_and_page(monkeypatch, integration_engine) -> None:
    from app.config import settings

    owner_tg_user_id = 93741
    target_tg_user_id = 93742
    monkeypatch.setattr(settings, "admin_user_ids", str(owner_tg_user_id))
    monkeypatch.setattr(settings, "admin_operator_user_ids", "")

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", session_factory)

    async with session_factory() as session:
        async with session.begin():
            target_user = User(tg_user_id=target_tg_user_id)
            session.add(target_user)
            await session.flush()

            for idx in range(12):
                session.add(
                    PointsLedgerEntry(
                        user_id=target_user.id,
                        amount=1,
                        event_type=PointsEventType.MANUAL_ADJUSTMENT,
                        dedupe_key=f"manual:history:{idx}",
                        reason=f"manual-{idx}",
                        payload=None,
                    )
                )
            session.add(
                PointsLedgerEntry(
                    user_id=target_user.id,
                    amount=30,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="feedback:history:1",
                    reason="feedback",
                    payload=None,
                )
            )

    message = _DummyMessage(
        text=f"/modpoints_history {target_tg_user_id} 2 manual",
        from_user_id=owner_tg_user_id,
    )
    await mod_points_history(message)

    assert message.answers
    reply = message.answers[-1]
    assert "фильтр: manual" in reply
    assert "стр. 2/2" in reply
    assert "Всего записей: 12" in reply


@pytest.mark.asyncio
async def test_modpoints_history_rejects_invalid_args(monkeypatch, integration_engine) -> None:
    from app.config import settings

    owner_tg_user_id = 93751
    target_tg_user_id = 93752
    monkeypatch.setattr(settings, "admin_user_ids", str(owner_tg_user_id))
    monkeypatch.setattr(settings, "admin_operator_user_ids", "")

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", session_factory)

    async with session_factory() as session:
        async with session.begin():
            target_user = User(tg_user_id=target_tg_user_id)
            session.add(target_user)

    message = _DummyMessage(text=f"/modpoints_history {target_tg_user_id} 0 weird", from_user_id=owner_tg_user_id)
    await mod_points_history(message)

    assert message.answers
    assert "Некорректная страница" in message.answers[-1] or "Формат:" in message.answers[-1]


@pytest.mark.asyncio
async def test_modpoints_history_denied_for_operator_without_scope(monkeypatch, integration_engine) -> None:
    from app.config import settings

    owner_tg_user_id = 93761
    operator_tg_user_id = 93762
    target_tg_user_id = 93763
    monkeypatch.setattr(settings, "admin_user_ids", f"{owner_tg_user_id},{operator_tg_user_id}")
    monkeypatch.setattr(settings, "admin_operator_user_ids", str(operator_tg_user_id))

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", session_factory)

    async with session_factory() as session:
        async with session.begin():
            target_user = User(tg_user_id=target_tg_user_id)
            session.add(target_user)

    message = _DummyMessage(text=f"/modpoints_history {target_tg_user_id}", from_user_id=operator_tg_user_id)
    await mod_points_history(message)

    assert message.answers
    assert "Недостаточно прав" in message.answers[-1]


@pytest.mark.asyncio
async def test_modstats_includes_points_utility_block(monkeypatch, integration_engine) -> None:
    from app.config import settings

    owner_tg_user_id = 93771
    target_tg_user_id = 93772
    monkeypatch.setattr(settings, "admin_user_ids", str(owner_tg_user_id))
    monkeypatch.setattr(settings, "admin_operator_user_ids", "")
    monkeypatch.setattr(settings, "feedback_priority_boost_enabled", True)
    monkeypatch.setattr(settings, "feedback_priority_boost_cost_points", 25)
    monkeypatch.setattr(settings, "feedback_priority_boost_daily_limit", 2)
    monkeypatch.setattr(settings, "feedback_priority_boost_cooldown_seconds", 11)
    monkeypatch.setattr(settings, "guarantor_priority_boost_enabled", False)
    monkeypatch.setattr(settings, "guarantor_priority_boost_cost_points", 40)
    monkeypatch.setattr(settings, "guarantor_priority_boost_daily_limit", 1)
    monkeypatch.setattr(settings, "guarantor_priority_boost_cooldown_seconds", 22)
    monkeypatch.setattr(settings, "appeal_priority_boost_enabled", True)
    monkeypatch.setattr(settings, "appeal_priority_boost_cost_points", 20)
    monkeypatch.setattr(settings, "appeal_priority_boost_daily_limit", 1)
    monkeypatch.setattr(settings, "appeal_priority_boost_cooldown_seconds", 33)
    monkeypatch.setattr(settings, "points_redemption_enabled", False)
    monkeypatch.setattr(settings, "points_redemption_daily_limit", 5)
    monkeypatch.setattr(settings, "points_redemption_daily_spend_cap", 80)
    monkeypatch.setattr(settings, "points_redemption_min_balance", 15)
    monkeypatch.setattr(settings, "points_redemption_min_account_age_seconds", 3600)
    monkeypatch.setattr(settings, "points_redemption_min_earned_points", 45)
    monkeypatch.setattr(settings, "points_redemption_cooldown_seconds", 77)

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", session_factory)

    async with session_factory() as session:
        async with session.begin():
            target_user = User(tg_user_id=target_tg_user_id)
            session.add(target_user)
            await session.flush()

            session.add(
                PointsLedgerEntry(
                    user_id=target_user.id,
                    amount=12,
                    event_type=PointsEventType.FEEDBACK_APPROVED,
                    dedupe_key="stats:points:earned",
                    reason="seed",
                    payload=None,
                    created_at=datetime.now(UTC),
                )
            )
            session.add(
                PointsLedgerEntry(
                    user_id=target_user.id,
                    amount=-4,
                    event_type=PointsEventType.FEEDBACK_PRIORITY_BOOST,
                    dedupe_key="stats:points:boost",
                    reason="seed",
                    payload=None,
                    created_at=datetime.now(UTC),
                )
            )

    message = _DummyMessage(text="/modstats", from_user_id=owner_tg_user_id)
    await mod_stats(message)

    assert message.answers
    text = message.answers[-1]
    assert "Points utility" in text
    assert "Активные points-пользователи (7д): 1" in text
    assert "Пользователи с положительным балансом: 1" in text
    assert "Редимеры points (7д): 1 (100.0%)" in text
    assert "Редимеры фидбек-буста (7д): 1" in text
    assert "Редимеры буста гаранта (7д): 0" in text
    assert "Редимеры буста апелляции (7д): 0" in text
    assert "Points начислено (24ч): +12" in text
    assert "Points списано (24ч): -4" in text
    assert "Бустов фидбека (24ч): 1" in text
    assert "Бустов гаранта (24ч): 0" in text
    assert "Бустов апелляций (24ч): 0" in text
    assert "Points policy" in text
    assert "feedback: on | cost 25 | limit 2/day | cooldown 11s" in text
    assert "guarantor: off | cost 40 | limit 1/day | cooldown 22s" in text
    assert "appeal: on | cost 20 | limit 1/day | cooldown 33s" in text
    assert "redemptions: off" in text
    assert "global daily limit: 5/day" in text
    assert "global daily spend cap: 80 points/day" in text
    assert "min balance after redemption: 15 points" in text
    assert "min account age for redemption: 3600s" in text
    assert "min earned points for redemption: 45 points" in text
    assert "global cooldown: 77s" in text


@pytest.mark.asyncio
async def test_modpoints_history_supports_guarantor_boost_filter(monkeypatch, integration_engine) -> None:
    from app.config import settings

    owner_tg_user_id = 93781
    target_tg_user_id = 93782
    monkeypatch.setattr(settings, "admin_user_ids", str(owner_tg_user_id))
    monkeypatch.setattr(settings, "admin_operator_user_ids", "")

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", session_factory)

    async with session_factory() as session:
        async with session.begin():
            target_user = User(tg_user_id=target_tg_user_id)
            session.add(target_user)
            await session.flush()

            session.add(
                PointsLedgerEntry(
                    user_id=target_user.id,
                    amount=-9,
                    event_type=PointsEventType.GUARANTOR_PRIORITY_BOOST,
                    dedupe_key="history:gboost:1",
                    reason="guarant boost",
                    payload=None,
                )
            )

    message = _DummyMessage(
        text=f"/modpoints_history {target_tg_user_id} 1 gboost",
        from_user_id=owner_tg_user_id,
    )
    await mod_points_history(message)

    assert message.answers
    reply = message.answers[-1]
    assert "фильтр: gboost" in reply
    assert "guarant boost" in reply


@pytest.mark.asyncio
async def test_modpoints_history_supports_appeal_boost_filter(monkeypatch, integration_engine) -> None:
    from app.config import settings

    owner_tg_user_id = 93791
    target_tg_user_id = 93792
    monkeypatch.setattr(settings, "admin_user_ids", str(owner_tg_user_id))
    monkeypatch.setattr(settings, "admin_operator_user_ids", "")

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", session_factory)

    async with session_factory() as session:
        async with session.begin():
            target_user = User(tg_user_id=target_tg_user_id)
            session.add(target_user)
            await session.flush()

            session.add(
                PointsLedgerEntry(
                    user_id=target_user.id,
                    amount=-7,
                    event_type=PointsEventType.APPEAL_PRIORITY_BOOST,
                    dedupe_key="history:aboost:1",
                    reason="appeal boost",
                    payload=None,
                )
            )

    message = _DummyMessage(
        text=f"/modpoints_history {target_tg_user_id} 1 aboost",
        from_user_id=owner_tg_user_id,
    )
    await mod_points_history(message)

    assert message.answers
    reply = message.answers[-1]
    assert "фильтр: aboost" in reply
    assert "appeal boost" in reply
