from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers.moderation import mod_panel_callbacks, mod_report_action, mod_risk_action
from app.db.enums import AppealStatus, AppealSourceType, AuctionStatus, ModerationAction
from app.db.models import Appeal, Auction, BlacklistEntry, Complaint, FraudSignal, ModerationLog, User
from app.services.timeline_service import build_auction_timeline


class _DummyFromUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.username = f"user{user_id}"
        self.first_name = "Test"
        self.last_name = "User"


class _DummyCallback:
    def __init__(self, *, data: str, from_user_id: int, message=None) -> None:
        self.data = data
        self.from_user = _DummyFromUser(from_user_id)
        self.message = message
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, *, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


class _DummyBot:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str, object | None]] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> None:
        self.sent_messages.append((chat_id, text, reply_markup))


class _DummyMessage:
    def __init__(self) -> None:
        self.edits: list[tuple[str, object | None]] = []

    async def edit_text(self, text: str, reply_markup=None) -> None:
        self.edits.append((text, reply_markup))


def _assert_timeline_sequence(timeline, expected_titles: list[str]) -> None:
    titles = [item.title for item in timeline]
    cursor = -1
    for expected in expected_titles:
        next_index = next((idx for idx in range(cursor + 1, len(titles)) if titles[idx] == expected), None)
        assert next_index is not None, f"Timeline does not include expected event: {expected}"
        cursor = next_index


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

    queue_message = _DummyMessage()
    callback = _DummyCallback(
        data=f"modrep:freeze:{complaint_id}",
        from_user_id=actor_tg_user_id,
        message=queue_message,
    )
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
    assert len(queue_message.edits) == 1
    updated_text, updated_reply_markup = queue_message.edits[-1]
    assert "Жалоба #" in updated_text
    assert "Статус: RESOLVED" in updated_text
    assert updated_reply_markup is not None

    async with session_factory() as session:
        _, timeline = await build_auction_timeline(session, auction_id)

    titles = [item.title for item in timeline]
    assert "Жалоба создана" in titles
    assert "Жалоба обработана" in titles
    assert "Мод-действие: FREEZE_AUCTION" in titles
    _assert_timeline_sequence(
        timeline,
        [
            "Жалоба создана",
            "Мод-действие: FREEZE_AUCTION",
            "Жалоба обработана",
        ],
    )
    assert [item.happened_at for item in timeline] == sorted(item.happened_at for item in timeline)
    complaint_resolved_item = next(item for item in timeline if item.title == "Жалоба обработана")
    assert "status=RESOLVED" in complaint_resolved_item.details
    assert "note=Заморозка аукциона" in complaint_resolved_item.details


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
            auction_id = auction.id

    queue_message = _DummyMessage()
    callback = _DummyCallback(
        data=f"modrep:ban_top:{complaint_id}",
        from_user_id=operator_tg_user_id,
        message=queue_message,
    )
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
    assert queue_message.edits == []

    async with session_factory() as session:
        _, timeline = await build_auction_timeline(session, auction_id)

    timeline_titles = [item.title for item in timeline]
    assert "Жалоба создана" in timeline_titles
    assert "Жалоба обработана" not in timeline_titles
    assert "Мод-действие: BAN_USER" not in timeline_titles


@pytest.mark.asyncio
async def test_modrisk_ban_updates_db_refresh_and_notifies(monkeypatch, integration_engine) -> None:
    from app.config import settings

    actor_tg_user_id = 72001
    monkeypatch.setattr(settings, "admin_user_ids", str(actor_tg_user_id))
    monkeypatch.setattr(settings, "admin_operator_user_ids", "")
    monkeypatch.setattr(settings, "bot_username", "liteauction_bot")

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

    queue_message = _DummyMessage()
    callback = _DummyCallback(
        data=f"modrisk:ban:{signal_id}",
        from_user_id=actor_tg_user_id,
        message=queue_message,
    )
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
    assert len(bot.sent_messages) == 1
    sent_chat_id, sent_text, sent_markup = bot.sent_messages[0]
    assert sent_chat_id == suspect_tg_user_id
    assert "Ваш аккаунт получил санкции" in sent_text
    assert f"#{signal_id}" in sent_text
    assert "апелляцию" in sent_text
    assert sent_markup is not None
    assert callback.answers[-1][0] == "Пользователь заблокирован"
    assert len(queue_message.edits) == 1
    updated_text, updated_reply_markup = queue_message.edits[-1]
    assert "Фрод-сигнал #" in updated_text
    assert "Статус: CONFIRMED" in updated_text
    assert "Решение: Пользователь заблокирован" in updated_text
    assert updated_reply_markup is not None

    async with session_factory() as session:
        _, timeline = await build_auction_timeline(session, auction_id)

    timeline_titles = [item.title for item in timeline]
    assert "Фрод-сигнал создан" in timeline_titles
    assert "Фрод-сигнал обработан" in timeline_titles
    assert "Мод-действие: BAN_USER" in timeline_titles
    _assert_timeline_sequence(
        timeline,
        [
            "Фрод-сигнал создан",
            "Мод-действие: BAN_USER",
            "Фрод-сигнал обработан",
        ],
    )
    assert [item.happened_at for item in timeline] == sorted(item.happened_at for item in timeline)
    signal_resolved_item = next(item for item in timeline if item.title == "Фрод-сигнал обработан")
    assert "status=CONFIRMED" in signal_resolved_item.details
    assert "note=Пользователь заблокирован" in signal_resolved_item.details


@pytest.mark.asyncio
async def test_modrep_freeze_repeat_click_is_idempotent(monkeypatch, integration_engine) -> None:
    from app.config import settings

    actor_tg_user_id = 73001
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
            seller = User(tg_user_id=73101, username="seller")
            reporter = User(tg_user_id=73102, username="reporter")
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

    first_message = _DummyMessage()
    first_callback = _DummyCallback(
        data=f"modrep:freeze:{complaint_id}",
        from_user_id=actor_tg_user_id,
        message=first_message,
    )
    bot = _DummyBot()
    await mod_report_action(first_callback, bot)

    second_message = _DummyMessage()
    second_callback = _DummyCallback(
        data=f"modrep:freeze:{complaint_id}",
        from_user_id=actor_tg_user_id,
        message=second_message,
    )
    await mod_report_action(second_callback, bot)

    assert refresh_calls == [auction_id]
    assert second_callback.answers
    assert second_callback.answers[-1][1] is True
    assert "Жалоба уже обработана" in (second_callback.answers[-1][0] or "")
    assert second_message.edits == []

    async with session_factory() as session:
        complaint_row = await session.scalar(select(Complaint).where(Complaint.id == complaint_id))
        freeze_logs = (
            await session.execute(
                select(ModerationLog).where(
                    ModerationLog.auction_id == auction_id,
                    ModerationLog.action == ModerationAction.FREEZE_AUCTION,
                )
            )
        ).scalars().all()
        _, timeline = await build_auction_timeline(session, auction_id)

    assert complaint_row is not None
    assert complaint_row.status == "RESOLVED"
    assert len(freeze_logs) == 1
    assert sum(1 for item in timeline if item.title == "Жалоба обработана") == 1
    assert sum(1 for item in timeline if item.title == "Мод-действие: FREEZE_AUCTION") == 1
    _assert_timeline_sequence(
        timeline,
        [
            "Жалоба создана",
            "Мод-действие: FREEZE_AUCTION",
            "Жалоба обработана",
        ],
    )


@pytest.mark.asyncio
async def test_modrisk_ban_repeat_click_is_idempotent(monkeypatch, integration_engine) -> None:
    from app.config import settings

    actor_tg_user_id = 74001
    monkeypatch.setattr(settings, "admin_user_ids", str(actor_tg_user_id))
    monkeypatch.setattr(settings, "admin_operator_user_ids", "")
    monkeypatch.setattr(settings, "bot_username", "liteauction_bot")

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", session_factory)

    refresh_calls: list[uuid.UUID] = []

    async def fake_refresh(_bot, auction_id: uuid.UUID) -> None:
        refresh_calls.append(auction_id)

    monkeypatch.setattr("app.bot.handlers.moderation.refresh_auction_posts", fake_refresh)

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=74101, username="seller")
            suspect = User(tg_user_id=74102, username="suspect")
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
                score=85,
                reasons={"rules": [{"code": "TEST", "detail": "risk", "score": 85}]},
                status="OPEN",
            )
            session.add(signal)
            await session.flush()
            signal_id = signal.id
            auction_id = auction.id
            suspect_user_id = suspect.id
            suspect_tg_user_id = suspect.tg_user_id

    first_message = _DummyMessage()
    first_callback = _DummyCallback(
        data=f"modrisk:ban:{signal_id}",
        from_user_id=actor_tg_user_id,
        message=first_message,
    )
    bot = _DummyBot()
    await mod_risk_action(first_callback, bot)

    second_message = _DummyMessage()
    second_callback = _DummyCallback(
        data=f"modrisk:ban:{signal_id}",
        from_user_id=actor_tg_user_id,
        message=second_message,
    )
    await mod_risk_action(second_callback, bot)

    assert refresh_calls == [auction_id]
    assert len(bot.sent_messages) == 1
    sent_chat_id, sent_text, sent_markup = bot.sent_messages[0]
    assert sent_chat_id == suspect_tg_user_id
    assert "санкции" in sent_text
    assert "апелляцию" in sent_text
    assert sent_markup is not None
    assert second_callback.answers
    assert second_callback.answers[-1][1] is True
    assert "Сигнал уже обработан" in (second_callback.answers[-1][0] or "")
    assert second_message.edits == []

    async with session_factory() as session:
        signal_row = await session.scalar(select(FraudSignal).where(FraudSignal.id == signal_id))
        ban_logs = (
            await session.execute(
                select(ModerationLog).where(
                    ModerationLog.auction_id == auction_id,
                    ModerationLog.action == ModerationAction.BAN_USER,
                )
            )
        ).scalars().all()
        active_blacklist_entries = (
            await session.execute(
                select(BlacklistEntry).where(
                    BlacklistEntry.user_id == suspect_user_id,
                    BlacklistEntry.is_active.is_(True),
                )
            )
        ).scalars().all()
        _, timeline = await build_auction_timeline(session, auction_id)

    assert signal_row is not None
    assert signal_row.status == "CONFIRMED"
    assert len(ban_logs) == 1
    assert len(active_blacklist_entries) == 1
    assert sum(1 for item in timeline if item.title == "Фрод-сигнал обработан") == 1
    assert sum(1 for item in timeline if item.title == "Мод-действие: BAN_USER") == 1
    _assert_timeline_sequence(
        timeline,
        [
            "Фрод-сигнал создан",
            "Мод-действие: BAN_USER",
            "Фрод-сигнал обработан",
        ],
    )


@pytest.mark.asyncio
async def test_modpanel_unfreeze_action_from_frozen_list(monkeypatch, integration_engine) -> None:
    from app.config import settings

    actor_tg_user_id = 75001
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
            seller = User(tg_user_id=75101, username="seller")
            session.add(seller)
            await session.flush()

            auction = Auction(
                seller_user_id=seller.id,
                description="lot",
                photo_file_id="photo",
                start_price=10,
                buyout_price=None,
                min_step=1,
                duration_hours=24,
                status=AuctionStatus.FROZEN,
            )
            session.add(auction)
            await session.flush()
            auction_id = auction.id
            seller_tg_user_id = seller.tg_user_id

    message = _DummyMessage()
    callback = _DummyCallback(
        data=f"modui:unfreeze:{auction_id}:0",
        from_user_id=actor_tg_user_id,
        message=message,
    )
    bot = _DummyBot()

    await mod_panel_callbacks(callback, bot)

    async with session_factory() as session:
        auction_row = await session.scalar(select(Auction).where(Auction.id == auction_id))

    assert auction_row is not None
    assert auction_row.status == AuctionStatus.ACTIVE
    assert refresh_calls == [auction_id]
    assert len(message.edits) == 1
    assert "Замороженные аукционы" in message.edits[0][0]
    assert callback.answers
    assert callback.answers[-1][0] == "Аукцион разморожен"

    assert bot.sent_messages
    sent_chat_id, sent_text, _ = bot.sent_messages[-1]
    assert sent_chat_id == seller_tg_user_id
    assert "разморожен модератором" in sent_text


@pytest.mark.asyncio
async def test_modpanel_appeal_review_updates_status(monkeypatch, integration_engine) -> None:
    from app.config import settings

    actor_tg_user_id = 75991
    monkeypatch.setattr(settings, "admin_user_ids", str(actor_tg_user_id))
    monkeypatch.setattr(settings, "admin_operator_user_ids", "")

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", session_factory)

    async with session_factory() as session:
        async with session.begin():
            appellant = User(tg_user_id=75992, username="appellant")
            session.add(appellant)
            await session.flush()

            appeal = Appeal(
                appeal_ref="manual_appeal_review",
                source_type=AppealSourceType.MANUAL,
                source_id=None,
                appellant_user_id=appellant.id,
                status=AppealStatus.OPEN,
            )
            session.add(appeal)
            await session.flush()
            appeal_id = appeal.id

    message = _DummyMessage()
    callback = _DummyCallback(
        data=f"modui:appeal_review:{appeal_id}:0",
        from_user_id=actor_tg_user_id,
        message=message,
    )
    bot = _DummyBot()

    await mod_panel_callbacks(callback, bot)

    async with session_factory() as session:
        appeal_row = await session.scalar(select(Appeal).where(Appeal.id == appeal_id))

    assert appeal_row is not None
    assert appeal_row.status == AppealStatus.IN_REVIEW
    assert appeal_row.resolver_user_id is not None
    assert appeal_row.resolution_note == "Взята в работу через modpanel"
    assert appeal_row.resolved_at is None

    assert callback.answers
    assert callback.answers[-1][0] == "Апелляция взята в работу"
    assert len(message.edits) == 1
    assert "Активные апелляции" in message.edits[0][0]
    assert bot.sent_messages == []


@pytest.mark.asyncio
async def test_modpanel_appeal_resolve_updates_status_and_notifies(monkeypatch, integration_engine) -> None:
    from app.config import settings

    actor_tg_user_id = 76001
    monkeypatch.setattr(settings, "admin_user_ids", str(actor_tg_user_id))
    monkeypatch.setattr(settings, "admin_operator_user_ids", "")

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", session_factory)

    async with session_factory() as session:
        async with session.begin():
            seller = User(tg_user_id=76100, username="seller")
            appellant = User(tg_user_id=76101, username="appellant")
            session.add_all([seller, appellant])
            await session.flush()

            auction = Auction(
                seller_user_id=seller.id,
                description="appeal lot",
                photo_file_id="photo",
                start_price=20,
                buyout_price=None,
                min_step=1,
                duration_hours=24,
                status=AuctionStatus.ACTIVE,
            )
            session.add(auction)
            await session.flush()

            signal = FraudSignal(
                auction_id=auction.id,
                user_id=appellant.id,
                bid_id=None,
                score=77,
                reasons={"rules": [{"code": "APPEAL", "detail": "risk", "score": 77}]},
                status="OPEN",
            )
            session.add(signal)
            await session.flush()

            appeal = Appeal(
                appeal_ref=f"risk_{signal.id}",
                source_type=AppealSourceType.RISK,
                source_id=signal.id,
                appellant_user_id=appellant.id,
                status=AppealStatus.OPEN,
            )
            session.add(appeal)
            await session.flush()
            appeal_id = appeal.id
            appellant_tg_user_id = appellant.tg_user_id
            appellant_user_id = appellant.id
            auction_id = auction.id

    message = _DummyMessage()
    callback = _DummyCallback(
        data=f"modui:appeal_resolve:{appeal_id}:0",
        from_user_id=actor_tg_user_id,
        message=message,
    )
    bot = _DummyBot()

    await mod_panel_callbacks(callback, bot)

    async with session_factory() as session:
        appeal_row = await session.scalar(select(Appeal).where(Appeal.id == appeal_id))
        audit_logs = (
            await session.execute(
                select(ModerationLog).where(
                    ModerationLog.action == ModerationAction.RESOLVE_APPEAL,
                    ModerationLog.target_user_id == appellant_user_id,
                    ModerationLog.auction_id == auction_id,
                )
            )
        ).scalars().all()

    assert appeal_row is not None
    assert appeal_row.status == AppealStatus.RESOLVED
    assert appeal_row.resolution_note == "Апелляция удовлетворена"
    assert appeal_row.resolver_user_id is not None
    assert appeal_row.resolved_at is not None
    assert len(audit_logs) == 1
    assert audit_logs[0].payload is not None
    assert audit_logs[0].payload.get("appeal_id") == appeal_id

    assert callback.answers
    assert callback.answers[-1][0] == "Апелляция удовлетворена"
    assert len(message.edits) == 1
    assert "Активные апелляции" in message.edits[0][0]

    assert len(bot.sent_messages) == 1
    sent_chat_id, sent_text, _ = bot.sent_messages[0]
    assert sent_chat_id == appellant_tg_user_id
    assert f"#{appeal_id}" in sent_text
    assert "удовлетворена" in sent_text


@pytest.mark.asyncio
async def test_modpanel_appeals_list_denied_for_operator_without_user_ban_scope(monkeypatch, integration_engine) -> None:
    from app.config import settings

    owner_tg_user_id = 77001
    operator_tg_user_id = 77002
    monkeypatch.setattr(settings, "admin_user_ids", f"{owner_tg_user_id},{operator_tg_user_id}")
    monkeypatch.setattr(settings, "admin_operator_user_ids", str(operator_tg_user_id))

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.moderation.SessionFactory", session_factory)

    message = _DummyMessage()
    callback = _DummyCallback(
        data="modui:appeals:0",
        from_user_id=operator_tg_user_id,
        message=message,
    )
    bot = _DummyBot()

    await mod_panel_callbacks(callback, bot)

    assert callback.answers
    assert callback.answers[-1][1] is True
    assert "Недостаточно прав" in (callback.answers[-1][0] or "")
    assert message.edits == []
