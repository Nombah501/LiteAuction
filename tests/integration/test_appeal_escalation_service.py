from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import AppealSourceType, AppealStatus, ModerationAction
from app.db.models import Appeal, ModerationLog, User
from aiogram import Bot
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
    monkeypatch.setattr(settings, "appeal_escalation_actor_tg_user_id", -99700)
    monkeypatch.setattr(settings, "moderation_chat_id", "-1007001")
    monkeypatch.setattr(settings, "moderation_thread_id", "42")
    monkeypatch.setattr(settings, "moderation_topic_appeals_id", "")
    monkeypatch.setattr(settings, "admin_user_ids", "99710,99711")

    bot = _DummyBot()
    escalated_count = await process_overdue_appeal_escalations(cast(Bot, bot))
    second_run_count = await process_overdue_appeal_escalations(cast(Bot, bot))

    assert escalated_count == 1
    assert second_run_count == 0
    assert len(bot.sent_messages) == 1

    sent_chat_id, sent_text, sent_kwargs = bot.sent_messages[0]
    assert sent_chat_id == -1007001
    assert sent_kwargs.get("message_thread_id") == 42
    assert "Эскалация апелляции" in sent_text
    assert "manual_overdue_notify" in sent_text

    async with session_factory() as session:
        appeals = (await session.execute(select(Appeal).order_by(Appeal.id.asc()))).scalars().all()
        logs = (
            await session.execute(
                select(ModerationLog)
                .where(ModerationLog.action == ModerationAction.ESCALATE_APPEAL)
                .order_by(ModerationLog.id.asc())
            )
        ).scalars().all()
        actor = await session.scalar(select(User).where(User.tg_user_id == -99700))

    assert appeals[0].escalated_at is not None
    assert appeals[0].escalation_level == 1
    assert appeals[1].escalated_at is None
    assert appeals[1].escalation_level == 0
    assert actor is not None
    assert len(logs) == 1
    assert logs[0].actor_user_id == actor.id
    assert logs[0].target_user_id == appeals[0].appellant_user_id
    assert logs[0].payload is not None
    assert logs[0].payload.get("appeal_id") == appeals[0].id
    assert logs[0].payload.get("escalation_level") == 1
