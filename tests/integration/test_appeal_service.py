from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import AppealSourceType, AppealStatus
from app.db.models import Appeal, User
from app.services.appeal_service import (
    create_appeal_from_ref,
    parse_appeal_ref,
    reject_appeal,
    resolve_appeal,
)


def test_parse_appeal_ref_mapping() -> None:
    assert parse_appeal_ref("complaint_51") == (AppealSourceType.COMPLAINT, 51)
    assert parse_appeal_ref("risk_18") == (AppealSourceType.RISK, 18)
    assert parse_appeal_ref("unknown_ref") == (AppealSourceType.MANUAL, None)
    assert parse_appeal_ref("risk_xyz") == (AppealSourceType.MANUAL, None)


@pytest.mark.asyncio
async def test_create_appeal_from_ref_persists_source_data(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            user = User(tg_user_id=88101, username="appeal_user")
            session.add(user)
            await session.flush()

            appeal = await create_appeal_from_ref(
                session,
                appellant_user_id=user.id,
                appeal_ref="complaint_73",
            )

        await session.refresh(appeal)

    assert appeal.appeal_ref == "complaint_73"
    assert appeal.source_type == AppealSourceType.COMPLAINT
    assert appeal.source_id == 73
    assert appeal.status == AppealStatus.OPEN


@pytest.mark.asyncio
async def test_create_appeal_from_ref_is_idempotent_per_user(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            user = User(tg_user_id=88201, username="appeal_user")
            session.add(user)
            await session.flush()

            first = await create_appeal_from_ref(
                session,
                appellant_user_id=user.id,
                appeal_ref="risk_99",
            )
            second = await create_appeal_from_ref(
                session,
                appellant_user_id=user.id,
                appeal_ref="risk_99",
            )

        appeal_rows = (
            await session.execute(
                select(Appeal).where(Appeal.appellant_user_id == user.id)
            )
        ).scalars().all()

    assert first.id == second.id
    assert len(appeal_rows) == 1


@pytest.mark.asyncio
async def test_finalize_appeal_transitions(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            appellant = User(tg_user_id=88301, username="appellant")
            resolver = User(tg_user_id=88302, username="resolver")
            session.add_all([appellant, resolver])
            await session.flush()

            appeal = await create_appeal_from_ref(
                session,
                appellant_user_id=appellant.id,
                appeal_ref="manual_anything",
            )

            resolve_result = await resolve_appeal(
                session,
                appeal_id=appeal.id,
                resolver_user_id=resolver.id,
                note="Проверено вручную",
            )

            repeat_result = await reject_appeal(
                session,
                appeal_id=appeal.id,
                resolver_user_id=resolver.id,
                note="Повторная обработка",
            )

    assert resolve_result.ok is True
    assert resolve_result.appeal is not None
    assert resolve_result.appeal.status == AppealStatus.RESOLVED
    assert resolve_result.appeal.resolved_at is not None
    assert resolve_result.appeal.resolution_note == "Проверено вручную"
    assert repeat_result.ok is False
    assert repeat_result.appeal is not None
    assert repeat_result.appeal.status == AppealStatus.RESOLVED
