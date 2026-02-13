from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import PointsEventType
from app.db.models import PointsLedgerEntry


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
) -> list[PointsLedgerEntry]:
    safe_limit = max(1, min(limit, 20))
    rows = await session.execute(
        select(PointsLedgerEntry)
        .where(PointsLedgerEntry.user_id == user_id)
        .order_by(PointsLedgerEntry.created_at.desc(), PointsLedgerEntry.id.desc())
        .limit(safe_limit)
    )
    return list(rows.scalars().all())
