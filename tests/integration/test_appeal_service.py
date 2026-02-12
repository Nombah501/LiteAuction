from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import AppealSourceType, AppealStatus
from app.db.models import Appeal, User
from app.services.appeal_service import (
    create_appeal_from_ref,
    escalate_overdue_appeals,
    mark_appeal_in_review,
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
    assert appeal.sla_deadline_at is not None
    assert appeal.escalated_at is None
    assert appeal.escalation_level == 0


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


@pytest.mark.asyncio
async def test_mark_appeal_in_review_allows_followup_finalize(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            appellant = User(tg_user_id=88401, username="appellant_review")
            reviewer = User(tg_user_id=88402, username="reviewer")
            resolver = User(tg_user_id=88403, username="resolver")
            session.add_all([appellant, reviewer, resolver])
            await session.flush()

            appeal = await create_appeal_from_ref(
                session,
                appellant_user_id=appellant.id,
                appeal_ref="manual_review_step",
            )

            review_result = await mark_appeal_in_review(
                session,
                appeal_id=appeal.id,
                reviewer_user_id=reviewer.id,
                note="Взята в работу",
            )

            assert review_result.ok is True
            assert review_result.appeal is not None
            assert review_result.appeal.status == AppealStatus.IN_REVIEW
            assert review_result.appeal.resolver_user_id == reviewer.id
            assert review_result.appeal.resolution_note == "Взята в работу"
            assert review_result.appeal.resolved_at is None
            assert review_result.appeal.in_review_started_at is not None
            assert review_result.appeal.sla_deadline_at is not None
            assert review_result.appeal.sla_deadline_at > review_result.appeal.in_review_started_at

            resolve_result = await resolve_appeal(
                session,
                appeal_id=appeal.id,
                resolver_user_id=resolver.id,
                note="Проверено",
            )

    assert resolve_result.ok is True
    assert resolve_result.appeal is not None
    assert resolve_result.appeal.status == AppealStatus.RESOLVED
    assert resolve_result.appeal.resolver_user_id == resolver.id
    assert resolve_result.appeal.resolution_note == "Проверено"
    assert resolve_result.appeal.resolved_at is not None
    assert resolve_result.appeal.sla_deadline_at is None


@pytest.mark.asyncio
async def test_escalate_overdue_appeals_is_one_time(integration_engine) -> None:
    session_factory = async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(UTC)

    async with session_factory() as session:
        async with session.begin():
            user = User(tg_user_id=88501, username="overdue_user")
            session.add(user)
            await session.flush()

            overdue_open = Appeal(
                appeal_ref="manual_overdue_open",
                source_type=AppealSourceType.MANUAL,
                source_id=None,
                appellant_user_id=user.id,
                status=AppealStatus.OPEN,
                sla_deadline_at=now - timedelta(minutes=10),
            )
            overdue_review = Appeal(
                appeal_ref="manual_overdue_review",
                source_type=AppealSourceType.MANUAL,
                source_id=None,
                appellant_user_id=user.id,
                status=AppealStatus.IN_REVIEW,
                in_review_started_at=now - timedelta(hours=2),
                sla_deadline_at=now - timedelta(minutes=1),
            )
            fresh_open = Appeal(
                appeal_ref="manual_fresh_open",
                source_type=AppealSourceType.MANUAL,
                source_id=None,
                appellant_user_id=user.id,
                status=AppealStatus.OPEN,
                sla_deadline_at=now + timedelta(hours=1),
            )
            resolved_due = Appeal(
                appeal_ref="manual_resolved_due",
                source_type=AppealSourceType.MANUAL,
                source_id=None,
                appellant_user_id=user.id,
                status=AppealStatus.RESOLVED,
                sla_deadline_at=now - timedelta(hours=1),
            )
            session.add_all([overdue_open, overdue_review, fresh_open, resolved_due])
            await session.flush()

            first_run = await escalate_overdue_appeals(session, now=now, limit=20)
            second_run = await escalate_overdue_appeals(session, now=now + timedelta(minutes=1), limit=20)

            refreshed = (
                await session.execute(
                    select(Appeal).where(Appeal.appellant_user_id == user.id).order_by(Appeal.id.asc())
                )
            ).scalars().all()

    assert {item.appeal_ref for item in first_run.escalated} == {
        "manual_overdue_open",
        "manual_overdue_review",
    }
    assert second_run.escalated == []

    by_ref = {item.appeal_ref: item for item in refreshed}
    assert by_ref["manual_overdue_open"].escalation_level == 1
    assert by_ref["manual_overdue_open"].escalated_at is not None
    assert by_ref["manual_overdue_review"].escalation_level == 1
    assert by_ref["manual_overdue_review"].escalated_at is not None
    assert by_ref["manual_fresh_open"].escalation_level == 0
    assert by_ref["manual_fresh_open"].escalated_at is None
    assert by_ref["manual_resolved_due"].escalation_level == 0
    assert by_ref["manual_resolved_due"].escalated_at is None
