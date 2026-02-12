from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers.feedback import feedback_callbacks
from app.db.enums import FeedbackStatus, FeedbackType, IntegrationOutboxStatus, ModerationAction
from app.db.models import FeedbackItem, IntegrationOutbox, ModerationLog, User
from app.services.outbox_service import OUTBOX_EVENT_FEEDBACK_APPROVED


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
async def test_feedback_callback_approve_updates_status_and_notifies(monkeypatch, integration_engine) -> None:
    from app.config import settings

    moderator_tg_user_id = 93101
    monkeypatch.setattr(settings, "admin_user_ids", str(moderator_tg_user_id))
    monkeypatch.setattr(settings, "admin_operator_user_ids", "")

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.bot.handlers.feedback.SessionFactory", session_factory)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93102, username="feedback_submitter")
            session.add(submitter)
            await session.flush()

            item = FeedbackItem(
                type=FeedbackType.SUGGESTION,
                status=FeedbackStatus.NEW,
                submitter_user_id=submitter.id,
                content="Добавьте поддержку узбекского языка",
            )
            session.add(item)
            await session.flush()
            feedback_id = item.id

    queue_message = _DummyMessage()
    callback = _DummyCallback(data=f"modfb:approve:{feedback_id}", from_user_id=moderator_tg_user_id, message=queue_message)
    bot = _DummyBot()

    await feedback_callbacks(callback, bot)

    async with session_factory() as session:
        item_row = await session.scalar(select(FeedbackItem).where(FeedbackItem.id == feedback_id))
        log_row = await session.scalar(
            select(ModerationLog)
            .where(ModerationLog.action == ModerationAction.APPROVE_FEEDBACK)
            .order_by(ModerationLog.id.desc())
        )
        outbox_row = await session.scalar(
            select(IntegrationOutbox)
            .where(IntegrationOutbox.event_type == OUTBOX_EVENT_FEEDBACK_APPROVED)
            .order_by(IntegrationOutbox.id.desc())
        )

    assert item_row is not None
    assert item_row.status == FeedbackStatus.APPROVED
    assert item_row.reward_points == 20
    assert item_row.resolved_at is not None

    assert log_row is not None
    assert log_row.payload is not None
    assert log_row.payload.get("feedback_id") == feedback_id
    assert log_row.payload.get("status") == FeedbackStatus.APPROVED

    assert outbox_row is not None
    assert outbox_row.status == IntegrationOutboxStatus.PENDING
    assert outbox_row.payload.get("feedback_id") == feedback_id

    assert queue_message.edits
    assert "Статус: APPROVED" in queue_message.edits[-1][0]
    assert callback.answers
    assert callback.answers[-1][0] == "Одобрено"
    assert bot.sent_messages
    assert bot.sent_messages[-1][0] == 93102
    assert "+20 points" in bot.sent_messages[-1][1]
