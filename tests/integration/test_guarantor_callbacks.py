from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers.guarantor import guarantor_callbacks
from app.db.enums import GuarantorRequestStatus, ModerationAction
from app.db.models import GuarantorRequest, ModerationLog, User


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


class _DummyMessage:
    def __init__(self) -> None:
        self.edits: list[tuple[str, object | None]] = []

    async def edit_text(self, text: str, reply_markup=None) -> None:
        self.edits.append((text, reply_markup))


class _DummyBot:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **_kwargs) -> None:
        self.sent_messages.append((chat_id, text))


@pytest.mark.asyncio
async def test_guarantor_callback_assign_updates_status_and_notifies(monkeypatch, integration_engine) -> None:
    from app.config import settings

    moderator_tg_user_id = 93401
    monkeypatch.setattr(settings, "admin_user_ids", str(moderator_tg_user_id))
    monkeypatch.setattr(settings, "admin_operator_user_ids", "")

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.guarantor.SessionFactory", session_factory)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93402, username="guarant_submitter")
            session.add(submitter)
            await session.flush()

            item = GuarantorRequest(
                status=GuarantorRequestStatus.NEW,
                submitter_user_id=submitter.id,
                details="Нужен гарант для офлайн сделки",
            )
            session.add(item)
            await session.flush()
            request_id = item.id

    queue_message = _DummyMessage()
    callback = _DummyCallback(data=f"modgr:assign:{request_id}", from_user_id=moderator_tg_user_id, message=queue_message)
    bot = _DummyBot()

    await guarantor_callbacks(callback, bot)

    async with session_factory() as session:
        item_row = await session.scalar(select(GuarantorRequest).where(GuarantorRequest.id == request_id))
        log_row = await session.scalar(
            select(ModerationLog)
            .where(ModerationLog.action == ModerationAction.ASSIGN_GUARANTOR_REQUEST)
            .order_by(ModerationLog.id.desc())
        )

    assert item_row is not None
    assert item_row.status == GuarantorRequestStatus.ASSIGNED
    assert item_row.moderator_user_id is not None
    assert item_row.resolved_at is not None

    assert log_row is not None
    assert log_row.payload is not None
    assert log_row.payload.get("guarantor_request_id") == request_id
    assert log_row.payload.get("status") == GuarantorRequestStatus.ASSIGNED

    assert queue_message.edits
    assert "Статус: ASSIGNED" in queue_message.edits[-1][0]
    assert callback.answers
    assert callback.answers[-1][0] == "Гарант назначен"
    assert bot.sent_messages
    assert bot.sent_messages[-1][0] == 93402
    assert "Ответственный модератор" in bot.sent_messages[-1][1]
