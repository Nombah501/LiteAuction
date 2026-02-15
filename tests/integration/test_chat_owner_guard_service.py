from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ChatOwnerServiceEventAudit
from app.services.chat_owner_guard_service import (
    EVENT_CHAT_OWNER_CHANGED,
    ChatOwnerServiceEvent,
    confirm_chat_owner_events,
    is_chat_owner_confirmation_required,
    record_chat_owner_service_event,
)
from app.services.user_service import upsert_user


class _FromUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.username = f"user{user_id}"
        self.first_name = "Guard"
        self.last_name = "Tester"


@pytest.mark.asyncio
async def test_chat_owner_guard_pause_and_confirm(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    chat_id = -100123000

    async with session_factory() as session:
        async with session.begin():
            actor = await upsert_user(session, _FromUser(99501), mark_private_started=True)
            event = ChatOwnerServiceEvent(
                event_type=EVENT_CHAT_OWNER_CHANGED,
                old_owner_tg_user_id=111,
                new_owner_tg_user_id=222,
                payload={"source": "test"},
            )
            persisted = await record_chat_owner_service_event(
                session,
                chat_id=chat_id,
                message_id=77,
                event=event,
            )

            assert persisted.created is True
            assert await is_chat_owner_confirmation_required(session, chat_id=chat_id) is True

            resolved = await confirm_chat_owner_events(
                session,
                chat_id=chat_id,
                actor_user_id=actor.id,
            )
            assert resolved == 1
            assert await is_chat_owner_confirmation_required(session, chat_id=chat_id) is False


@pytest.mark.asyncio
async def test_chat_owner_guard_deduplicates_same_service_message(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    chat_id = -100123001

    async with session_factory() as session:
        async with session.begin():
            event = ChatOwnerServiceEvent(
                event_type=EVENT_CHAT_OWNER_CHANGED,
                old_owner_tg_user_id=11,
                new_owner_tg_user_id=33,
                payload={"source": "duplicate-test"},
            )
            first = await record_chat_owner_service_event(
                session,
                chat_id=chat_id,
                message_id=99,
                event=event,
            )
            second = await record_chat_owner_service_event(
                session,
                chat_id=chat_id,
                message_id=99,
                event=event,
            )

            assert first.created is True
            assert second.created is False

            count = await session.scalar(
                select(func.count(ChatOwnerServiceEventAudit.id)).where(
                    ChatOwnerServiceEventAudit.chat_id == chat_id,
                    ChatOwnerServiceEventAudit.message_id == 99,
                    ChatOwnerServiceEventAudit.event_type == EVENT_CHAT_OWNER_CHANGED,
                )
            )
            assert count == 1
