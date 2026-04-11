from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import AuctionStatus, ReputationEventReason, ReputationTier
from app.db.models import (
    Auction,
    Complaint,
    FraudSignal,
    User,
    UserReputation,
    UserReputationEvent,
)
from app.services.guarantor_service import has_assigned_guarantor_request
from app.services.verification_service import is_user_verified

_INITIAL_BASE_NEW = 10
_INITIAL_BASE_ESTABLISHED = 25

_DELTA_SELLER_COMPLETED = 5
_MAX_SELLER_COMPLETED = 30
_DELTA_BUYER_WON = 3
_MAX_BUYER_WON = 30
_DELTA_COMPLAINT = -10
_MAX_COMPLAINT = -30
_DELTA_FRAUD_CONFIRMED = -20
_MAX_FRAUD_CONFIRMED = -40
_DELTA_VERIFIED = 15
_MAX_VERIFIED = 15
_DELTA_GUARANTOR = 10
_MAX_GUARANTOR = 10

_DAILY_POSITIVE_CAP = 15
_DAILY_NEGATIVE_CAP = -40
_DECAY_FLOOR = 10
_DECAY_INTERVAL_DAYS = 30
_DECAY_DELTA = -2

_TIER_THRESHOLDS: list[tuple[int, str]] = [
    (81, ReputationTier.PLATINUM),
    (61, ReputationTier.GOLD),
    (41, ReputationTier.SILVER),
    (21, ReputationTier.BRONZE),
    (0, ReputationTier.NEW),
]


def _score_to_tier(score: int) -> str:
    for threshold, tier in _TIER_THRESHOLDS:
        if score >= threshold:
            return tier
    return ReputationTier.NEW


async def _calculate_initial_score(session: AsyncSession, user: User) -> int:
    now = datetime.now(UTC)
    base = (
        _INITIAL_BASE_NEW
        if (now - user.created_at) < timedelta(hours=24)
        else _INITIAL_BASE_ESTABLISHED
    )

    completed_statuses = (AuctionStatus.ENDED, AuctionStatus.BOUGHT_OUT)

    seller_count = await session.scalar(
        select(func.count(Auction.id)).where(
            Auction.seller_user_id == user.id,
            Auction.status.in_(completed_statuses),
        )
    )
    seller_bonus = min(seller_count * _DELTA_SELLER_COMPLETED, _MAX_SELLER_COMPLETED)

    buyer_count = await session.scalar(
        select(func.count(Auction.id)).where(
            Auction.winner_user_id == user.id,
            Auction.status.in_(completed_statuses),
        )
    )
    buyer_bonus = min(buyer_count * _DELTA_BUYER_WON, _MAX_BUYER_WON)

    complaint_count = await session.scalar(
        select(func.count(Complaint.id)).where(
            Complaint.target_user_id == user.id,
        )
    )
    complaint_penalty = max(complaint_count * _DELTA_COMPLAINT, _MAX_COMPLAINT)

    fraud_count = await session.scalar(
        select(func.count(FraudSignal.id)).where(
            FraudSignal.user_id == user.id,
            FraudSignal.status == "CONFIRMED",
        )
    )
    fraud_penalty = max(fraud_count * _DELTA_FRAUD_CONFIRMED, _MAX_FRAUD_CONFIRMED)

    verified_bonus = 0
    if await is_user_verified(session, tg_user_id=user.tg_user_id):
        verified_bonus = _DELTA_VERIFIED

    guarantor_bonus = 0
    if await has_assigned_guarantor_request(
        session, submitter_user_id=user.id, max_age_days=365
    ):
        guarantor_bonus = _DELTA_GUARANTOR

    raw = (
        base
        + seller_bonus
        + buyer_bonus
        + complaint_penalty
        + fraud_penalty
        + verified_bonus
        + guarantor_bonus
    )
    return max(0, min(raw, 100))


async def get_or_create_reputation(
    session: AsyncSession, user_id: int
) -> UserReputation:
    rep = await session.scalar(
        select(UserReputation).where(UserReputation.user_id == user_id)
    )
    if rep is not None:
        return rep

    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise ValueError(f"User {user_id} not found")

    score = await _calculate_initial_score(session, user)
    now = datetime.now(UTC)
    rep = UserReputation(
        user_id=user_id,
        score=score,
        tier=_score_to_tier(score),
        last_event_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(rep)

    event = UserReputationEvent(
        user_id=user_id,
        delta=score,
        reason="initial",
        source_id=None,
        created_at=now,
    )
    session.add(event)
    await session.flush()

    rep.last_event_at = now
    rep.updated_at = now
    return rep


async def recalculate_user_reputation(
    session: AsyncSession, user_id: int
) -> UserReputation:
    rep = await get_or_create_reputation(session, user_id)
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        return rep

    score = await _calculate_initial_score(session, user)

    event_delta = await session.scalar(
        select(func.coalesce(func.sum(UserReputationEvent.delta), 0)).where(
            UserReputationEvent.user_id == user_id,
            UserReputationEvent.reason != "initial",
            UserReputationEvent.reason != ReputationEventReason.DECAY,
        )
    )
    score = max(0, min(score + (event_delta or 0), 100))

    old_tier = rep.tier
    new_tier = _score_to_tier(score)
    rep.score = score
    rep.tier = new_tier
    rep.updated_at = datetime.now(UTC)
    if old_tier != new_tier:
        rep._tier_changed = True
        rep._old_tier = old_tier

    await session.flush()
    return rep


async def _daily_delta_used(session: AsyncSession, user_id: int) -> tuple[int, int]:
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    row = await session.execute(
        select(
            func.coalesce(
                func.sum(
                    UserReputationEvent.delta
                ).filter(
                    UserReputationEvent.delta > 0
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    UserReputationEvent.delta
                ).filter(
                    UserReputationEvent.delta < 0
                ),
                0,
            ),
        ).where(
            UserReputationEvent.user_id == user_id,
            UserReputationEvent.created_at >= cutoff,
        )
    )
    positive_used, negative_used = row.one()
    return positive_used, negative_used


async def adjust_reputation(
    session: AsyncSession,
    user_id: int,
    delta: int,
    reason: str,
    source_id: int | None = None,
) -> UserReputation | None:
    rep = await session.scalar(
        select(UserReputation).where(UserReputation.user_id == user_id)
    )
    if rep is None:
        return None

    now = datetime.now(UTC)

    if reason == ReputationEventReason.PERM_BAN:
        actual_delta = -rep.score
        rep.score = 0
    else:
        positive_used, negative_used = await _daily_delta_used(session, user_id)

        if delta > 0:
            remaining = _DAILY_POSITIVE_CAP - positive_used
            if remaining <= 0:
                return None
            actual_delta = min(delta, remaining)
        elif delta < 0:
            remaining = _DAILY_NEGATIVE_CAP - negative_used
            if remaining >= 0:
                return None
            actual_delta = max(delta, remaining)
        else:
            return None

        rep.score = max(0, min(rep.score + actual_delta, 100))

    old_tier = rep.tier
    new_tier = _score_to_tier(rep.score)
    rep.tier = new_tier
    rep.last_event_at = now
    rep.updated_at = now

    if old_tier != new_tier:
        rep._tier_changed = True
        rep._old_tier = old_tier

    event = UserReputationEvent(
        user_id=user_id,
        delta=actual_delta,
        reason=reason,
        source_id=source_id,
        created_at=now,
    )
    session.add(event)
    await session.flush()
    return rep


async def get_user_tier(session: AsyncSession, user_id: int) -> str:
    rep = await session.scalar(
        select(UserReputation).where(UserReputation.user_id == user_id)
    )
    if rep is not None:
        return rep.tier
    return ReputationTier.NEW


async def get_reputation_history(
    session: AsyncSession, user_id: int, limit: int = 20
) -> list[UserReputationEvent]:
    result = await session.execute(
        select(UserReputationEvent)
        .where(UserReputationEvent.user_id == user_id)
        .order_by(desc(UserReputationEvent.created_at))
        .limit(limit)
    )
    return list(result.scalars().all())


async def run_reputation_decay(session: AsyncSession) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=_DECAY_INTERVAL_DAYS)
    result = await session.execute(
        select(UserReputation).where(
            UserReputation.last_event_at.is_not(None),
            UserReputation.last_event_at < cutoff,
            UserReputation.score > _DECAY_FLOOR,
        )
    )
    reps = list(result.scalars().all())
    if not reps:
        return 0

    now = datetime.now(UTC)
    affected = 0

    for rep in reps:
        last = rep.last_event_at
        if last is None:
            continue

        elapsed = now - last
        gaps = elapsed.days // _DECAY_INTERVAL_DAYS
        if gaps <= 0:
            continue

        decay_amount = gaps * _DECAY_DELTA
        new_score = max(_DECAY_FLOOR, rep.score + decay_amount)
        if new_score == rep.score:
            continue

        actual_delta = new_score - rep.score
        old_tier = rep.tier

        rep.score = new_score
        rep.tier = _score_to_tier(new_score)
        rep.last_event_at = now
        rep.updated_at = now

        if old_tier != rep.tier:
            rep._tier_changed = True
            rep._old_tier = old_tier

        event = UserReputationEvent(
            user_id=rep.user_id,
            delta=actual_delta,
            reason=ReputationEventReason.DECAY,
            source_id=None,
            created_at=now,
        )
        session.add(event)
        affected += 1

    await session.flush()
    return affected
