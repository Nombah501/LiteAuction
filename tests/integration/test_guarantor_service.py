from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import GuarantorRequestStatus
from app.db.models import GuarantorRequest, User
from app.services.guarantor_service import (
    assign_guarantor_request,
    create_guarantor_request,
    reject_guarantor_request,
)


@pytest.mark.asyncio
async def test_guarantor_service_full_transition(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93301, username="guarant_submitter")
            moderator = User(tg_user_id=93302, username="guarant_mod")
            session.add_all([submitter, moderator])
            await session.flush()

            created = await create_guarantor_request(
                session,
                submitter_user_id=submitter.id,
                details="Нужен гарант для сделки по лоту #123, сумма 12000",
            )
            assert created.ok is True
            assert created.item is not None

            assigned = await assign_guarantor_request(
                session,
                request_id=created.item.id,
                moderator_user_id=moderator.id,
                note="Беру как гарант",
            )
            assert assigned.ok is True
            assert assigned.changed is True
            assert assigned.item is not None
            assert assigned.item.status == GuarantorRequestStatus.ASSIGNED

            assigned_again = await assign_guarantor_request(
                session,
                request_id=created.item.id,
                moderator_user_id=moderator.id,
                note="дубликат",
            )
            assert assigned_again.ok is True
            assert assigned_again.changed is False

            rejected_after_assign = await reject_guarantor_request(
                session,
                request_id=created.item.id,
                moderator_user_id=moderator.id,
                note="не нужно",
            )
            assert rejected_after_assign.ok is False

    async with session_factory() as session:
        row = await session.scalar(select(GuarantorRequest).where(GuarantorRequest.submitter_user_id == submitter.id))

    assert row is not None
    assert row.status == GuarantorRequestStatus.ASSIGNED
    assert row.resolved_at is not None


@pytest.mark.asyncio
async def test_guarantor_create_respects_cooldown(monkeypatch, integration_engine) -> None:
    from app.config import settings

    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "guarantor_intake_cooldown_seconds", 3600)

    async with session_factory() as session:
        async with session.begin():
            submitter = User(tg_user_id=93311, username="guarant_cooldown")
            session.add(submitter)
            await session.flush()

            first = await create_guarantor_request(
                session,
                submitter_user_id=submitter.id,
                details="Первый запрос на гаранта для сделки",
            )
            second = await create_guarantor_request(
                session,
                submitter_user_id=submitter.id,
                details="Повторный запрос сразу",
            )

    assert first.ok is True
    assert second.ok is False
    assert "Слишком часто" in second.message
