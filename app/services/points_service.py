from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import PointsEventType
from app.db.models import PointsLedgerEntry, User

BOOST_REDEMPTION_EVENT_TYPES: tuple[PointsEventType, ...] = (
    PointsEventType.FEEDBACK_PRIORITY_BOOST,
    PointsEventType.GUARANTOR_PRIORITY_BOOST,
    PointsEventType.APPEAL_PRIORITY_BOOST,
)


@dataclass(slots=True)
class PointsGrantResult:
    changed: bool
    entry: PointsLedgerEntry | None


@dataclass(slots=True)
class UserPointsSummary:
    balance: int
    total_earned: int
    total_spent: int
    operations_count: int


@dataclass(slots=True)
class PointsSpendResult:
    ok: bool
    message: str
    changed: bool
    entry: PointsLedgerEntry | None
    balance_before: int
    balance_after: int


def feedback_reward_dedupe_key(feedback_id: int) -> str:
    return f"feedback:{feedback_id}:reward"


async def grant_points(
    session: AsyncSession,
    *,
    user_id: int,
    amount: int,
    event_type: PointsEventType,
    dedupe_key: str,
    reason: str,
    payload: dict | None = None,
) -> PointsGrantResult:
    if amount == 0:
        return PointsGrantResult(changed=False, entry=None)

    now = datetime.now(UTC)
    stmt = (
        insert(PointsLedgerEntry)
        .values(
            user_id=user_id,
            amount=amount,
            event_type=event_type,
            dedupe_key=dedupe_key,
            reason=reason,
            payload=payload,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=[PointsLedgerEntry.dedupe_key])
        .returning(PointsLedgerEntry.id)
    )
    inserted_id = await session.scalar(stmt)
    if inserted_id is None:
        existing = await session.scalar(
            select(PointsLedgerEntry).where(PointsLedgerEntry.dedupe_key == dedupe_key)
        )
        return PointsGrantResult(changed=False, entry=existing)

    entry = await session.scalar(select(PointsLedgerEntry).where(PointsLedgerEntry.id == inserted_id))
    return PointsGrantResult(changed=True, entry=entry)


async def get_user_points_balance(session: AsyncSession, *, user_id: int) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(PointsLedgerEntry.amount), 0)).where(PointsLedgerEntry.user_id == user_id)
    )
    return int(total or 0)


async def get_user_points_summary(session: AsyncSession, *, user_id: int) -> UserPointsSummary:
    balance, total_earned, total_spent, operations_count = (
        await session.execute(
            select(
                func.coalesce(func.sum(PointsLedgerEntry.amount), 0),
                func.coalesce(
                    func.sum(
                        case(
                            (PointsLedgerEntry.amount > 0, PointsLedgerEntry.amount),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (PointsLedgerEntry.amount < 0, -PointsLedgerEntry.amount),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.count(PointsLedgerEntry.id),
            ).where(PointsLedgerEntry.user_id == user_id)
        )
    ).one()

    return UserPointsSummary(
        balance=int(balance or 0),
        total_earned=int(total_earned or 0),
        total_spent=int(total_spent or 0),
        operations_count=int(operations_count or 0),
    )


async def list_user_points_entries(
    session: AsyncSession,
    *,
    user_id: int,
    limit: int = 5,
    offset: int = 0,
    event_type: PointsEventType | None = None,
) -> list[PointsLedgerEntry]:
    safe_limit = max(1, min(limit, 50))
    safe_offset = max(offset, 0)
    stmt = select(PointsLedgerEntry).where(PointsLedgerEntry.user_id == user_id)
    if event_type is not None:
        stmt = stmt.where(PointsLedgerEntry.event_type == event_type)

    rows = await session.execute(
        stmt.order_by(PointsLedgerEntry.created_at.desc(), PointsLedgerEntry.id.desc())
        .offset(safe_offset)
        .limit(safe_limit)
    )
    return list(rows.scalars().all())


async def count_user_points_entries(
    session: AsyncSession,
    *,
    user_id: int,
    event_type: PointsEventType | None = None,
) -> int:
    stmt = select(func.count(PointsLedgerEntry.id)).where(PointsLedgerEntry.user_id == user_id)
    if event_type is not None:
        stmt = stmt.where(PointsLedgerEntry.event_type == event_type)
    count = await session.scalar(stmt)
    return int(count or 0)


async def spend_points(
    session: AsyncSession,
    *,
    user_id: int,
    amount: int,
    event_type: PointsEventType,
    dedupe_key: str,
    reason: str,
    payload: dict | None = None,
) -> PointsSpendResult:
    spend_amount = abs(int(amount))
    if spend_amount <= 0:
        return PointsSpendResult(
            ok=False,
            message="Сумма списания должна быть больше 0",
            changed=False,
            entry=None,
            balance_before=0,
            balance_after=0,
        )

    user_exists = await session.scalar(select(User.id).where(User.id == user_id).with_for_update())
    if user_exists is None:
        return PointsSpendResult(
            ok=False,
            message="Пользователь не найден",
            changed=False,
            entry=None,
            balance_before=0,
            balance_after=0,
        )

    balance_before = await get_user_points_balance(session, user_id=user_id)
    if balance_before < spend_amount:
        return PointsSpendResult(
            ok=False,
            message="Недостаточно points",
            changed=False,
            entry=None,
            balance_before=balance_before,
            balance_after=balance_before,
        )

    grant_result = await grant_points(
        session,
        user_id=user_id,
        amount=-spend_amount,
        event_type=event_type,
        dedupe_key=dedupe_key,
        reason=reason,
        payload=payload,
    )
    if not grant_result.changed and grant_result.entry is None:
        return PointsSpendResult(
            ok=False,
            message="Списание не удалось",
            changed=False,
            entry=None,
            balance_before=balance_before,
            balance_after=balance_before,
        )

    if not grant_result.changed and grant_result.entry is not None:
        amount_value = int(grant_result.entry.amount)
        if amount_value < 0:
            balance_after = balance_before + amount_value
            return PointsSpendResult(
                ok=True,
                message="Списание уже применено",
                changed=False,
                entry=grant_result.entry,
                balance_before=balance_before,
                balance_after=balance_after,
            )
        return PointsSpendResult(
            ok=False,
            message="Конфликт dedupe_key",
            changed=False,
            entry=grant_result.entry,
            balance_before=balance_before,
            balance_after=balance_before,
        )

    balance_after = balance_before - spend_amount
    return PointsSpendResult(
        ok=True,
        message="Points списаны",
        changed=True,
        entry=grant_result.entry,
        balance_before=balance_before,
        balance_after=balance_after,
    )


async def get_points_redemption_cooldown_remaining_seconds(
    session: AsyncSession,
    *,
    user_id: int,
    cooldown_seconds: int,
    now: datetime | None = None,
) -> int:
    safe_cooldown = max(int(cooldown_seconds), 0)
    if safe_cooldown <= 0:
        return 0

    current_time = now or datetime.now(UTC)
    last_redemption_at = await session.scalar(
        select(func.max(PointsLedgerEntry.created_at)).where(
            PointsLedgerEntry.user_id == user_id,
            PointsLedgerEntry.amount < 0,
            PointsLedgerEntry.event_type.in_(BOOST_REDEMPTION_EVENT_TYPES),
        )
    )
    if last_redemption_at is None:
        return 0

    elapsed_seconds = (current_time - last_redemption_at).total_seconds()
    if elapsed_seconds >= safe_cooldown:
        return 0

    return max(math.ceil(safe_cooldown - elapsed_seconds), 0)


async def get_points_event_redemption_cooldown_remaining_seconds(
    session: AsyncSession,
    *,
    user_id: int,
    event_types: tuple[PointsEventType, ...],
    cooldown_seconds: int,
    now: datetime | None = None,
) -> int:
    safe_cooldown = max(int(cooldown_seconds), 0)
    if safe_cooldown <= 0 or not event_types:
        return 0

    current_time = now or datetime.now(UTC)
    last_redemption_at = await session.scalar(
        select(func.max(PointsLedgerEntry.created_at)).where(
            PointsLedgerEntry.user_id == user_id,
            PointsLedgerEntry.amount < 0,
            PointsLedgerEntry.event_type.in_(event_types),
        )
    )
    if last_redemption_at is None:
        return 0

    elapsed_seconds = (current_time - last_redemption_at).total_seconds()
    if elapsed_seconds >= safe_cooldown:
        return 0

    return max(math.ceil(safe_cooldown - elapsed_seconds), 0)


async def get_points_redemption_account_age_remaining_seconds(
    session: AsyncSession,
    *,
    user_id: int,
    min_account_age_seconds: int,
    now: datetime | None = None,
) -> int:
    safe_min_age = max(int(min_account_age_seconds), 0)
    if safe_min_age <= 0:
        return 0

    current_time = now or datetime.now(UTC)
    created_at = await session.scalar(select(User.created_at).where(User.id == user_id))
    if created_at is None:
        return 0
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)

    elapsed_seconds = (current_time - created_at).total_seconds()
    if elapsed_seconds >= safe_min_age:
        return 0

    return max(math.ceil(safe_min_age - elapsed_seconds), 0)


async def get_points_redemptions_used_today(
    session: AsyncSession,
    *,
    user_id: int,
    now: datetime | None = None,
) -> int:
    current_time = now or datetime.now(UTC)
    day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
    used_today = await session.scalar(
        select(func.count(PointsLedgerEntry.id)).where(
            PointsLedgerEntry.user_id == user_id,
            PointsLedgerEntry.amount < 0,
            PointsLedgerEntry.event_type.in_(BOOST_REDEMPTION_EVENT_TYPES),
            PointsLedgerEntry.created_at >= day_start,
        )
    )
    return int(used_today or 0)


async def get_points_redemptions_used_this_week(
    session: AsyncSession,
    *,
    user_id: int,
    now: datetime | None = None,
) -> int:
    current_time = now or datetime.now(UTC)
    day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=day_start.weekday())
    used_this_week = await session.scalar(
        select(func.count(PointsLedgerEntry.id)).where(
            PointsLedgerEntry.user_id == user_id,
            PointsLedgerEntry.amount < 0,
            PointsLedgerEntry.event_type.in_(BOOST_REDEMPTION_EVENT_TYPES),
            PointsLedgerEntry.created_at >= week_start,
        )
    )
    return int(used_this_week or 0)


async def get_points_redemptions_spent_today(
    session: AsyncSession,
    *,
    user_id: int,
    now: datetime | None = None,
) -> int:
    current_time = now or datetime.now(UTC)
    day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
    spent_today = await session.scalar(
        select(func.coalesce(func.sum(-PointsLedgerEntry.amount), 0)).where(
            PointsLedgerEntry.user_id == user_id,
            PointsLedgerEntry.amount < 0,
            PointsLedgerEntry.event_type.in_(BOOST_REDEMPTION_EVENT_TYPES),
            PointsLedgerEntry.created_at >= day_start,
        )
    )
    return int(spent_today or 0)


async def get_points_redemptions_spent_this_week(
    session: AsyncSession,
    *,
    user_id: int,
    now: datetime | None = None,
) -> int:
    current_time = now or datetime.now(UTC)
    day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=day_start.weekday())
    spent_this_week = await session.scalar(
        select(func.coalesce(func.sum(-PointsLedgerEntry.amount), 0)).where(
            PointsLedgerEntry.user_id == user_id,
            PointsLedgerEntry.amount < 0,
            PointsLedgerEntry.event_type.in_(BOOST_REDEMPTION_EVENT_TYPES),
            PointsLedgerEntry.created_at >= week_start,
        )
    )
    return int(spent_this_week or 0)


async def get_points_redemptions_spent_this_month(
    session: AsyncSession,
    *,
    user_id: int,
    now: datetime | None = None,
) -> int:
    current_time = now or datetime.now(UTC)
    month_start = current_time.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent_this_month = await session.scalar(
        select(func.coalesce(func.sum(-PointsLedgerEntry.amount), 0)).where(
            PointsLedgerEntry.user_id == user_id,
            PointsLedgerEntry.amount < 0,
            PointsLedgerEntry.event_type.in_(BOOST_REDEMPTION_EVENT_TYPES),
            PointsLedgerEntry.created_at >= month_start,
        )
    )
    return int(spent_this_month or 0)
