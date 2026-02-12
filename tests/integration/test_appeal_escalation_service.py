from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import AppealSourceType, AppealStatus
from app.db.models import Appeal, User
from app.services.appeal_escalation_service import process_overdue_appeal_escalations


class _DummyBot:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str, dict[str, int]]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.sent_messages.append((chat_id, text, kwargs))


@pytest.mark.asyncio
async def test_process_overdue_appeal_escalations_marks_and_notifies(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    now = datetime.now(UTC)
    async with session_factory() as session:
        async with session.begin():
            appellant = User(tg_user_id=99701, username="escalate_user")
            reviewer = User(tg_user_id=99702, username="reviewer_user")
            session.add_all([appellant, reviewer])
            await session.flush()

            session.add_all(
                [
                    Appeal(
                        appeal_ref="manual_overdue_notify",
                        source_type=AppealSourceType.MANUAL,
                        source_id=None,
                        appellant_user_id=appellant.id,
                        status=AppealStatus.IN_REVIEW,
                        resolver_user_id=reviewer.id,
                        in_review_started_at=now - timedelta(hours=3),
                        sla_deadline_at=now - timedelta(minutes=5),
                    ),
                    Appeal(
                        appeal_ref="manual_fresh_notify",
                        source_type=AppealSourceType.MANUAL,
                        source_id=None,
                        appellant_user_id=appellant.id,
                        status=AppealStatus.OPEN,
                        sla_deadline_at=now + timedelta(hours=2),
                    ),
                ]
            )

    monkeypatch.setattr("app.services.appeal_escalation_service.SessionFactory", session_factory)
    monkeypatch.setattr(settings, "appeal_escalation_enabled", True)
    monkeypatch.setattr(settings, "appeal_escalation_batch_size", 20)
    monkeypatch.setattr(settings, "moderation_chat_id", "-1007001")
    monkeypatch.setattr(settings, "moderation_thread_id", "42")
    monkeypatch.setattr(settings, "admin_user_ids", "99710,99711")

    bot = _DummyBot()
    escalated_count = await process_overdue_appeal_escalations(bot)

    assert escalated_count == 1
    assert len(bot.sent_messages) == 1

    sent_chat_id, sent_text, sent_kwargs = bot.sent_messages[0]
    assert sent_chat_id == -1007001
    assert sent_kwargs.get("message_thread_id") == 42
    assert "Эскалация апелляции" in sent_text
    assert "manual_overdue_notify" in sent_text

    async with session_factory() as session:
        appeals = (await session.execute(select(Appeal).order_by(Appeal.id.asc()))).scalars().all()

    assert appeals[0].escalated_at is not None
    assert appeals[0].escalation_level == 1
    assert appeals[1].escalated_at is None
    assert appeals[1].escalation_level == 0
