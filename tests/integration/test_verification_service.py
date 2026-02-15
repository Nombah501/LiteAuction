from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.user_service import upsert_user
from app.services.verification_service import (
    get_chat_verification_status,
    get_user_verification_status,
    load_verified_user_ids,
    set_chat_verification,
    set_user_verification,
)


class _FromUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.username = f"user{user_id}"
        self.first_name = "Ver"
        self.last_name = "Tester"


class _BotStub:
    async def verify_user(self, *, user_id: int, custom_description: str | None = None) -> bool:  # noqa: ARG002
        return True

    async def remove_user_verification(self, *, user_id: int) -> bool:  # noqa: ARG002
        return True

    async def verify_chat(self, *, chat_id: int, custom_description: str | None = None) -> bool:  # noqa: ARG002
        return True

    async def remove_chat_verification(self, *, chat_id: int) -> bool:  # noqa: ARG002
        return True


@pytest.mark.asyncio
async def test_verification_service_updates_user_and_chat_state(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    bot = _BotStub()

    async with session_factory() as session:
        async with session.begin():
            actor = await upsert_user(session, _FromUser(99701), mark_private_started=True)

            user_result = await set_user_verification(
                session,
                bot,
                actor_user_id=actor.id,
                target_tg_user_id=99711,
                verify=True,
                custom_description="trusted operator note",
            )
            assert user_result.ok is True

            user_status = await get_user_verification_status(session, tg_user_id=99711)
            assert user_status.is_verified is True
            assert user_status.custom_description == "trusted operator note"

            chat_result = await set_chat_verification(
                session,
                bot,
                actor_user_id=actor.id,
                chat_id=-10099711,
                verify=True,
                custom_description="official auction room",
            )
            assert chat_result.ok is True

            chat_status = await get_chat_verification_status(session, chat_id=-10099711)
            assert chat_status.is_verified is True
            assert chat_status.custom_description == "official auction room"

            remove_user = await set_user_verification(
                session,
                bot,
                actor_user_id=actor.id,
                target_tg_user_id=99711,
                verify=False,
            )
            assert remove_user.ok is True
            user_status_after = await get_user_verification_status(session, tg_user_id=99711)
            assert user_status_after.is_verified is False


@pytest.mark.asyncio
async def test_load_verified_user_ids_returns_only_verified_users(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    bot = _BotStub()

    async with session_factory() as session:
        async with session.begin():
            actor = await upsert_user(session, _FromUser(99731), mark_private_started=True)
            verified_user = await upsert_user(session, _FromUser(99732), mark_private_started=True)
            plain_user = await upsert_user(session, _FromUser(99733), mark_private_started=True)

            result = await set_user_verification(
                session,
                bot,
                actor_user_id=actor.id,
                target_tg_user_id=verified_user.tg_user_id,
                verify=True,
            )
            assert result.ok is True

            verified_ids = await load_verified_user_ids(
                session,
                user_ids=[verified_user.id, plain_user.id],
            )

            assert verified_ids == {verified_user.id}
