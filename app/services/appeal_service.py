from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.enums import AppealSourceType, AppealStatus, PointsEventType
from app.db.models import Appeal, Complaint, FraudSignal
from app.services.points_service import (
    get_points_event_redemption_cooldown_remaining_seconds,
    get_points_redemptions_used_today,
    get_points_redemption_cooldown_remaining_seconds,
    get_user_points_balance,
    spend_points,
)


@dataclass(slots=True)
class AppealResolveResult:
    ok: bool
    message: str
    appeal: Appeal | None = None


@dataclass(slots=True)
class AppealEscalationResult:
    escalated: list[Appeal]


@dataclass(slots=True)
class AppealPriorityBoostResult:
    ok: bool
    message: str
    appeal: Appeal | None = None
    changed: bool = False


@dataclass(slots=True)
class AppealPriorityBoostPolicy:
    enabled: bool
    cost_points: int
    daily_limit: int
    used_today: int
    remaining_today: int
    cooldown_seconds: int
    cooldown_remaining_seconds: int


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


def _open_sla_delta() -> timedelta:
    hours = max(settings.appeal_sla_open_hours, 1)
    return timedelta(hours=hours)


def _in_review_sla_delta() -> timedelta:
    hours = max(settings.appeal_sla_in_review_hours, 1)
    return timedelta(hours=hours)


def _compute_open_deadline(base_time: datetime) -> datetime:
    return base_time + _open_sla_delta()


def _compute_in_review_deadline(base_time: datetime) -> datetime:
    return base_time + _in_review_sla_delta()


def _is_active_status(status: AppealStatus) -> bool:
    return status in {AppealStatus.OPEN, AppealStatus.IN_REVIEW}


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
    now = datetime.now(UTC)
    try:
        async with session.begin_nested():
            appeal = Appeal(
                appeal_ref=normalized_ref,
                source_type=source_type,
                source_id=source_id,
                appellant_user_id=appellant_user_id,
                status=AppealStatus.OPEN,
                sla_deadline_at=_compute_open_deadline(now),
                escalation_level=0,
                updated_at=now,
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
    appeal.in_review_started_at = now
    appeal.sla_deadline_at = _compute_in_review_deadline(now)

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
    appeal.sla_deadline_at = None
    appeal.updated_at = now
    return AppealResolveResult(True, "Апелляция обработана", appeal=appeal)


async def escalate_overdue_appeals(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 50,
) -> AppealEscalationResult:
    current_time = now or datetime.now(UTC)
    batch_size = max(limit, 1)

    overdue_stmt = (
        select(Appeal)
        .where(
            Appeal.status.in_([AppealStatus.OPEN, AppealStatus.IN_REVIEW]),
            Appeal.escalated_at.is_(None),
            Appeal.sla_deadline_at.is_not(None),
            Appeal.sla_deadline_at <= current_time,
        )
        .order_by(Appeal.sla_deadline_at.asc(), Appeal.id.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    overdue = list((await session.execute(overdue_stmt)).scalars().all())
    if not overdue:
        return AppealEscalationResult(escalated=[])

    for appeal in overdue:
        status = AppealStatus(appeal.status)
        if not _is_active_status(status):
            continue
        if appeal.escalated_at is not None:
            continue
        appeal.escalated_at = current_time
        appeal.escalation_level = 1
        appeal.updated_at = current_time

    escalated = [item for item in overdue if item.escalated_at == current_time]
    return AppealEscalationResult(escalated=escalated)


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


async def get_appeal_priority_boost_policy(
    session: AsyncSession,
    *,
    appellant_user_id: int,
    now: datetime | None = None,
) -> AppealPriorityBoostPolicy:
    current_time = now or datetime.now(UTC)
    day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)

    daily_limit = max(settings.appeal_priority_boost_daily_limit, 1)
    cost_points = max(settings.appeal_priority_boost_cost_points, 1)
    cooldown_seconds = max(settings.appeal_priority_boost_cooldown_seconds, 0)
    used_today = int(
        await session.scalar(
            select(func.count(Appeal.id)).where(
                Appeal.appellant_user_id == appellant_user_id,
                Appeal.priority_boosted_at.is_not(None),
                Appeal.priority_boosted_at >= day_start,
            )
        )
        or 0
    )
    remaining_today = max(daily_limit - used_today, 0)
    cooldown_remaining_seconds = await get_points_event_redemption_cooldown_remaining_seconds(
        session,
        user_id=appellant_user_id,
        event_types=(PointsEventType.APPEAL_PRIORITY_BOOST,),
        cooldown_seconds=cooldown_seconds,
        now=current_time,
    )
    return AppealPriorityBoostPolicy(
        enabled=settings.appeal_priority_boost_enabled,
        cost_points=cost_points,
        daily_limit=daily_limit,
        used_today=used_today,
        remaining_today=remaining_today,
        cooldown_seconds=cooldown_seconds,
        cooldown_remaining_seconds=cooldown_remaining_seconds,
    )


async def redeem_appeal_priority_boost(
    session: AsyncSession,
    *,
    appeal_id: int,
    appellant_user_id: int,
) -> AppealPriorityBoostResult:
    appeal = await load_appeal(session, appeal_id, for_update=True)
    if appeal is None:
        return AppealPriorityBoostResult(False, "Апелляция не найдена")

    if appeal.appellant_user_id != appellant_user_id:
        return AppealPriorityBoostResult(False, "Можно бустить только свои апелляции")

    status = AppealStatus(appeal.status)
    if status not in {AppealStatus.OPEN, AppealStatus.IN_REVIEW}:
        return AppealPriorityBoostResult(False, "Буст доступен только для активных апелляций")

    if appeal.priority_boosted_at is not None:
        return AppealPriorityBoostResult(True, "Приоритет уже повышен", appeal=appeal, changed=False)

    now = datetime.now(UTC)
    policy = await get_appeal_priority_boost_policy(session, appellant_user_id=appellant_user_id, now=now)
    if not policy.enabled:
        return AppealPriorityBoostResult(False, "Буст апелляции временно отключен")
    if policy.cooldown_remaining_seconds > 0:
        return AppealPriorityBoostResult(
            False,
            f"Повторный буст апелляции доступен через {policy.cooldown_remaining_seconds} сек",
        )
    if policy.remaining_today <= 0:
        return AppealPriorityBoostResult(False, f"Достигнут дневной лимит бустов ({policy.daily_limit})")

    global_daily_limit = max(settings.points_redemption_daily_limit, 0)
    if global_daily_limit > 0:
        used_today = await get_points_redemptions_used_today(
            session,
            user_id=appellant_user_id,
            now=now,
        )
        if used_today >= global_daily_limit:
            return AppealPriorityBoostResult(
                False,
                f"Достигнут глобальный дневной лимит бустов ({global_daily_limit})",
            )

    cooldown_remaining = await get_points_redemption_cooldown_remaining_seconds(
        session,
        user_id=appellant_user_id,
        cooldown_seconds=settings.points_redemption_cooldown_seconds,
        now=now,
    )
    if cooldown_remaining > 0:
        return AppealPriorityBoostResult(False, f"Следующий буст доступен через {cooldown_remaining} сек")

    cost = policy.cost_points
    spend_result = await spend_points(
        session,
        user_id=appellant_user_id,
        amount=cost,
        event_type=PointsEventType.APPEAL_PRIORITY_BOOST,
        dedupe_key=f"boostap:{appeal_id}:{appellant_user_id}",
        reason=f"Буст апелляции #{appeal_id}",
        payload={"appeal_id": appeal_id, "utility": "appeal_priority_boost", "cost": cost},
    )
    if not spend_result.ok:
        current_balance = await get_user_points_balance(session, user_id=appellant_user_id)
        return AppealPriorityBoostResult(
            False,
            f"Недостаточно points для буста (нужно {cost}, доступно {current_balance})",
        )

    appeal.priority_boost_points_spent = cost
    appeal.priority_boosted_at = now
    appeal.updated_at = now
    return AppealPriorityBoostResult(True, "Приоритет апелляции повышен", appeal=appeal, changed=True)
