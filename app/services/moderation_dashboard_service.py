from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import AuctionStatus, PointsEventType
from app.db.models import Auction, Bid, BlacklistEntry, Complaint, FraudSignal, PointsLedgerEntry, User


@dataclass(slots=True)
class ModerationDashboardSnapshot:
    open_complaints: int
    open_signals: int
    active_auctions: int
    frozen_auctions: int
    bids_last_hour: int
    bids_last_24h: int
    active_blacklist_entries: int
    total_users: int
    users_private_started: int
    users_with_bid_activity: int
    users_with_report_activity: int
    users_with_engagement: int
    users_engaged_without_private_start: int
    users_with_soft_gate_hint: int
    users_soft_gate_hint_last_24h: int
    users_converted_after_hint: int
    users_pending_after_hint: int
    points_active_users_7d: int
    points_users_with_positive_balance: int
    points_redeemers_7d: int
    points_feedback_boost_redeemers_7d: int
    points_guarantor_boost_redeemers_7d: int
    points_earned_24h: int
    points_spent_24h: int
    feedback_boost_redeems_24h: int


async def get_moderation_dashboard_snapshot(session: AsyncSession) -> ModerationDashboardSnapshot:
    now = datetime.now(UTC)
    one_hour = now - timedelta(hours=1)
    one_day = now - timedelta(hours=24)
    seven_days = now - timedelta(days=7)

    engaged_users_subq = (
        select(Bid.user_id.label("user_id"))
        .where(Bid.is_removed.is_(False))
        .union(select(Complaint.reporter_user_id.label("user_id")))
        .subquery()
    )

    open_complaints = (
        await session.scalar(select(func.count(Complaint.id)).where(Complaint.status == "OPEN"))
    ) or 0
    open_signals = (
        await session.scalar(select(func.count(FraudSignal.id)).where(FraudSignal.status == "OPEN"))
    ) or 0
    active_auctions = (
        await session.scalar(select(func.count(Auction.id)).where(Auction.status == AuctionStatus.ACTIVE))
    ) or 0
    frozen_auctions = (
        await session.scalar(select(func.count(Auction.id)).where(Auction.status == AuctionStatus.FROZEN))
    ) or 0
    bids_last_hour = (
        await session.scalar(
            select(func.count(Bid.id)).where(Bid.is_removed.is_(False), Bid.created_at >= one_hour)
        )
    ) or 0
    bids_last_24h = (
        await session.scalar(
            select(func.count(Bid.id)).where(Bid.is_removed.is_(False), Bid.created_at >= one_day)
        )
    ) or 0
    active_blacklist_entries = (
        await session.scalar(
            select(func.count(BlacklistEntry.id)).where(
                BlacklistEntry.is_active.is_(True),
                (BlacklistEntry.expires_at.is_(None) | (BlacklistEntry.expires_at > now)),
            )
        )
    ) or 0

    total_users = (await session.scalar(select(func.count(User.id)))) or 0
    users_private_started = (
        await session.scalar(select(func.count(User.id)).where(User.private_started_at.is_not(None)))
    ) or 0
    users_with_bid_activity = (
        await session.scalar(select(func.count(func.distinct(Bid.user_id))).where(Bid.is_removed.is_(False)))
    ) or 0
    users_with_report_activity = (
        await session.scalar(select(func.count(func.distinct(Complaint.reporter_user_id))))
    ) or 0
    users_with_engagement = (
        await session.scalar(select(func.count()).select_from(engaged_users_subq))
    ) or 0
    users_engaged_without_private_start = (
        await session.scalar(
            select(func.count())
            .select_from(engaged_users_subq)
            .join(User, User.id == engaged_users_subq.c.user_id)
            .where(User.private_started_at.is_(None))
        )
    ) or 0
    users_with_soft_gate_hint = (
        await session.scalar(select(func.count(User.id)).where(User.soft_gate_hint_sent_at.is_not(None)))
    ) or 0
    users_soft_gate_hint_last_24h = (
        await session.scalar(
            select(func.count(User.id)).where(
                User.soft_gate_hint_sent_at.is_not(None),
                User.soft_gate_hint_sent_at >= one_day,
            )
        )
    ) or 0
    users_converted_after_hint = (
        await session.scalar(
            select(func.count(User.id)).where(
                User.soft_gate_hint_sent_at.is_not(None),
                User.private_started_at.is_not(None),
                User.private_started_at >= User.soft_gate_hint_sent_at,
            )
        )
    ) or 0
    users_pending_after_hint = (
        await session.scalar(
            select(func.count(User.id)).where(
                User.soft_gate_hint_sent_at.is_not(None),
                (User.private_started_at.is_(None) | (User.private_started_at < User.soft_gate_hint_sent_at)),
            )
        )
    ) or 0

    points_active_users_7d = (
        await session.scalar(
            select(func.count(func.distinct(PointsLedgerEntry.user_id))).where(PointsLedgerEntry.created_at >= seven_days)
        )
    ) or 0
    users_with_positive_balance_subq = (
        select(PointsLedgerEntry.user_id)
        .group_by(PointsLedgerEntry.user_id)
        .having(func.sum(PointsLedgerEntry.amount) > 0)
        .subquery()
    )
    points_users_with_positive_balance = (
        await session.scalar(select(func.count()).select_from(users_with_positive_balance_subq))
    ) or 0
    points_redeemers_7d = (
        await session.scalar(
            select(func.count(func.distinct(PointsLedgerEntry.user_id))).where(
                PointsLedgerEntry.created_at >= seven_days,
                PointsLedgerEntry.amount < 0,
            )
        )
    ) or 0
    points_feedback_boost_redeemers_7d = (
        await session.scalar(
            select(func.count(func.distinct(PointsLedgerEntry.user_id))).where(
                PointsLedgerEntry.created_at >= seven_days,
                PointsLedgerEntry.event_type == PointsEventType.FEEDBACK_PRIORITY_BOOST,
            )
        )
    ) or 0
    points_guarantor_boost_redeemers_7d = (
        await session.scalar(
            select(func.count(func.distinct(PointsLedgerEntry.user_id))).where(
                PointsLedgerEntry.created_at >= seven_days,
                PointsLedgerEntry.event_type == PointsEventType.GUARANTOR_PRIORITY_BOOST,
            )
        )
    ) or 0
    points_earned_24h = (
        await session.scalar(
            select(func.coalesce(func.sum(PointsLedgerEntry.amount), 0)).where(
                PointsLedgerEntry.created_at >= one_day,
                PointsLedgerEntry.amount > 0,
            )
        )
    ) or 0
    points_spent_24h = (
        await session.scalar(
            select(func.coalesce(func.sum(-PointsLedgerEntry.amount), 0)).where(
                PointsLedgerEntry.created_at >= one_day,
                PointsLedgerEntry.amount < 0,
            )
        )
    ) or 0
    feedback_boost_redeems_24h = (
        await session.scalar(
            select(func.count(PointsLedgerEntry.id)).where(
                PointsLedgerEntry.created_at >= one_day,
                PointsLedgerEntry.event_type == PointsEventType.FEEDBACK_PRIORITY_BOOST,
            )
        )
    ) or 0

    return ModerationDashboardSnapshot(
        open_complaints=int(open_complaints),
        open_signals=int(open_signals),
        active_auctions=int(active_auctions),
        frozen_auctions=int(frozen_auctions),
        bids_last_hour=int(bids_last_hour),
        bids_last_24h=int(bids_last_24h),
        active_blacklist_entries=int(active_blacklist_entries),
        total_users=int(total_users),
        users_private_started=int(users_private_started),
        users_with_bid_activity=int(users_with_bid_activity),
        users_with_report_activity=int(users_with_report_activity),
        users_with_engagement=int(users_with_engagement),
        users_engaged_without_private_start=int(users_engaged_without_private_start),
        users_with_soft_gate_hint=int(users_with_soft_gate_hint),
        users_soft_gate_hint_last_24h=int(users_soft_gate_hint_last_24h),
        users_converted_after_hint=int(users_converted_after_hint),
        users_pending_after_hint=int(users_pending_after_hint),
        points_active_users_7d=int(points_active_users_7d),
        points_users_with_positive_balance=int(points_users_with_positive_balance),
        points_redeemers_7d=int(points_redeemers_7d),
        points_feedback_boost_redeemers_7d=int(points_feedback_boost_redeemers_7d),
        points_guarantor_boost_redeemers_7d=int(points_guarantor_boost_redeemers_7d),
        points_earned_24h=int(points_earned_24h),
        points_spent_24h=int(points_spent_24h),
        feedback_boost_redeems_24h=int(feedback_boost_redeems_24h),
    )
