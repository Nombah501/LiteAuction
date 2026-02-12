from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import AppealSourceType, AppealStatus
from app.db.models import Appeal, Complaint, FraudSignal


@dataclass(slots=True)
class AppealResolveResult:
    ok: bool
    message: str
    appeal: Appeal | None = None


def parse_appeal_ref(appeal_ref: str) -> tuple[AppealSourceType, int | None]:
    normalized = appeal_ref.strip()
    if normalized.startswith("complaint_"):
        suffix = normalized[len("complaint_") :]
        if suffix.isdigit():
            return AppealSourceType.COMPLAINT, int(suffix)
    if normalized.startswith("risk_"):
        suffix = normalized[len("risk_") :]
        if suffix.isdigit():
            return AppealSourceType.RISK, int(suffix)
    return AppealSourceType.MANUAL, None


async def create_appeal_from_ref(
    session: AsyncSession,
    *,
    appellant_user_id: int,
    appeal_ref: str,
) -> Appeal:
    normalized_ref = appeal_ref.strip()
    if not normalized_ref:
        normalized_ref = "manual"

    existing = await session.scalar(
        select(Appeal)
        .where(
            Appeal.appellant_user_id == appellant_user_id,
            Appeal.appeal_ref == normalized_ref,
        )
        .with_for_update()
    )
    if existing is not None:
        return existing

    source_type, source_id = parse_appeal_ref(normalized_ref)
    try:
        async with session.begin_nested():
            appeal = Appeal(
                appeal_ref=normalized_ref,
                source_type=source_type,
                source_id=source_id,
                appellant_user_id=appellant_user_id,
                status=AppealStatus.OPEN,
            )
            session.add(appeal)
            await session.flush()
            return appeal
    except IntegrityError:
        existing_after_conflict = await session.scalar(
            select(Appeal).where(
                Appeal.appellant_user_id == appellant_user_id,
                Appeal.appeal_ref == normalized_ref,
            )
        )
        if existing_after_conflict is not None:
            return existing_after_conflict
        raise


async def load_appeal(
    session: AsyncSession,
    appeal_id: int,
    *,
    for_update: bool = False,
) -> Appeal | None:
    stmt = select(Appeal).where(Appeal.id == appeal_id)
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


async def list_appeals(
    session: AsyncSession,
    *,
    status: AppealStatus | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[Appeal]:
    stmt = (
        select(Appeal)
        .order_by(Appeal.created_at.desc())
        .offset(max(offset, 0))
        .limit(max(limit, 1))
    )
    if status is not None:
        stmt = stmt.where(Appeal.status == status)
    return list((await session.execute(stmt)).scalars().all())


async def resolve_appeal_auction_id(session: AsyncSession, appeal: Appeal) -> uuid.UUID | None:
    source_type = AppealSourceType(appeal.source_type)
    if source_type == AppealSourceType.COMPLAINT and appeal.source_id is not None:
        return await session.scalar(select(Complaint.auction_id).where(Complaint.id == appeal.source_id))
    if source_type == AppealSourceType.RISK and appeal.source_id is not None:
        return await session.scalar(select(FraudSignal.auction_id).where(FraudSignal.id == appeal.source_id))
    return None


def _can_finalize(status: AppealStatus) -> bool:
    return status in {AppealStatus.OPEN, AppealStatus.IN_REVIEW}


def _can_start_review(status: AppealStatus) -> bool:
    return status == AppealStatus.OPEN


async def mark_appeal_in_review(
    session: AsyncSession,
    *,
    appeal_id: int,
    reviewer_user_id: int,
    note: str,
) -> AppealResolveResult:
    appeal = await load_appeal(session, appeal_id, for_update=True)
    if appeal is None:
        return AppealResolveResult(False, "Апелляция не найдена")

    current_status = AppealStatus(appeal.status)
    if current_status == AppealStatus.IN_REVIEW:
        return AppealResolveResult(True, "Апелляция уже в работе", appeal=appeal)
    if not _can_start_review(current_status):
        return AppealResolveResult(False, "Апелляция уже закрыта", appeal=appeal)

    now = datetime.now(UTC)
    appeal.status = AppealStatus.IN_REVIEW
    appeal.resolver_user_id = reviewer_user_id

    review_note = note.strip()
    if review_note:
        appeal.resolution_note = review_note

    appeal.updated_at = now
    return AppealResolveResult(True, "Апелляция взята в работу", appeal=appeal)


async def finalize_appeal(
    session: AsyncSession,
    *,
    appeal_id: int,
    resolver_user_id: int,
    status: AppealStatus,
    note: str,
) -> AppealResolveResult:
    if status not in {AppealStatus.RESOLVED, AppealStatus.REJECTED}:
        raise ValueError("Appeal can only be finalized as RESOLVED or REJECTED")

    appeal = await load_appeal(session, appeal_id, for_update=True)
    if appeal is None:
        return AppealResolveResult(False, "Апелляция не найдена")
    if not _can_finalize(AppealStatus(appeal.status)):
        return AppealResolveResult(False, "Апелляция уже обработана", appeal=appeal)

    now = datetime.now(UTC)
    appeal.status = status
    appeal.resolver_user_id = resolver_user_id
    appeal.resolution_note = note.strip() or None
    appeal.resolved_at = now
    appeal.updated_at = now
    return AppealResolveResult(True, "Апелляция обработана", appeal=appeal)


async def resolve_appeal(
    session: AsyncSession,
    *,
    appeal_id: int,
    resolver_user_id: int,
    note: str,
) -> AppealResolveResult:
    return await finalize_appeal(
        session,
        appeal_id=appeal_id,
        resolver_user_id=resolver_user_id,
        status=AppealStatus.RESOLVED,
        note=note,
    )


async def reject_appeal(
    session: AsyncSession,
    *,
    appeal_id: int,
    resolver_user_id: int,
    note: str,
) -> AppealResolveResult:
    return await finalize_appeal(
        session,
        appeal_id=appeal_id,
        resolver_user_id=resolver_user_id,
        status=AppealStatus.REJECTED,
        note=note,
    )
