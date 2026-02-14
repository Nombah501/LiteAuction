from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.enums import (
    AppealStatus,
    AppealSourceType,
    AuctionStatus,
    FeedbackStatus,
    FeedbackType,
    GuarantorRequestStatus,
    IntegrationOutboxStatus,
    ModerationAction,
    PointsEventType,
    UserRole,
)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tg_user_id", name="uq_users_tg_user_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    private_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    soft_gate_hint_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_notifications_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )


class UserRoleAssignment(Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role", name="uq_user_roles_user_id_role"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("TIMEZONE('utc', NOW())"), nullable=False
    )


class Auction(Base, TimestampMixin):
    __tablename__ = "auctions"
    __table_args__ = (
        CheckConstraint("start_price >= 1", name="auctions_start_price_positive"),
        CheckConstraint("min_step >= 1", name="auctions_min_step_positive"),
        CheckConstraint("buyout_price IS NULL OR buyout_price >= start_price", name="auctions_buyout_gte_start"),
        CheckConstraint("duration_hours IN (6, 12, 18, 24)", name="auctions_duration_options"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    photo_file_id: Mapped[str] = mapped_column(Text, nullable=False)
    start_price: Mapped[int] = mapped_column(Integer, nullable=False)
    buyout_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_step: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_hours: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    anti_sniper_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    anti_sniper_extensions_used: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    anti_sniper_max_extensions: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="3"
    )
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[AuctionStatus] = mapped_column(
        Enum(AuctionStatus, name="auction_status"),
        nullable=False,
        default=AuctionStatus.DRAFT,
        server_default=text("'DRAFT'"),
    )
    winner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class Bid(Base):
    __tablename__ = "bids"
    __table_args__ = (CheckConstraint("amount >= 1", name="bids_amount_positive"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    auction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auctions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    is_removed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    removed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    removed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("TIMEZONE('utc', NOW())"), nullable=False
    )


class AuctionPost(Base):
    __tablename__ = "auction_posts"
    __table_args__ = (
        UniqueConstraint("auction_id", "chat_id", "message_id", name="uq_auction_posts_message"),
        UniqueConstraint("inline_message_id", name="uq_auction_posts_inline_message_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    auction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auctions.id", ondelete="CASCADE"), nullable=False
    )
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    inline_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("TIMEZONE('utc', NOW())"), nullable=False
    )


class BlacklistEntry(Base):
    __tablename__ = "blacklist_entries"
    __table_args__ = (UniqueConstraint("user_id", name="uq_blacklist_entries_user_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("TIMEZONE('utc', NOW())"), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class ModerationLog(Base):
    __tablename__ = "moderation_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    target_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    auction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auctions.id", ondelete="SET NULL"), nullable=True
    )
    bid_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bids.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[ModerationAction] = mapped_column(
        Enum(ModerationAction, name="moderation_action"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("TIMEZONE('utc', NOW())"), nullable=False, index=True
    )


class Complaint(Base):
    __tablename__ = "complaints"
    __table_args__ = (
        CheckConstraint("status IN ('OPEN', 'RESOLVED', 'DISMISSED')", name="complaints_status_values"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    auction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auctions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reporter_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    target_bid_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bids.id", ondelete="SET NULL"), nullable=True
    )
    target_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'OPEN'"), index=True)
    queue_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    queue_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resolved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("TIMEZONE('utc', NOW())"), nullable=False, index=True
    )


class Appeal(Base, TimestampMixin):
    __tablename__ = "appeals"
    __table_args__ = (
        UniqueConstraint("appellant_user_id", "appeal_ref", name="uq_appeals_appellant_user_id_appeal_ref"),
        CheckConstraint(
            "status IN ('OPEN', 'IN_REVIEW', 'RESOLVED', 'REJECTED')",
            name="appeals_status_values",
        ),
        CheckConstraint(
            "source_type IN ('complaint', 'risk', 'manual')",
            name="appeals_source_type_values",
        ),
        CheckConstraint(
            "((source_type = 'manual' AND source_id IS NULL) "
            "OR (source_type IN ('complaint', 'risk') AND source_id IS NOT NULL))",
            name="appeals_source_consistency",
        ),
        Index("ix_appeals_status_created_at", "status", "created_at"),
        Index("ix_appeals_source_type_source_id", "source_type", "source_id"),
        Index("ix_appeals_escalation_scan", "status", "escalated_at", "sla_deadline_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    appeal_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[AppealSourceType] = mapped_column(String(32), nullable=False)
    source_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    appellant_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[AppealStatus] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'OPEN'"),
        index=True,
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolver_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    in_review_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    escalation_level: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")


class FeedbackItem(Base, TimestampMixin):
    __tablename__ = "feedback_items"
    __table_args__ = (
        Index("ix_feedback_items_type_status_created_at", "type", "status", "created_at"),
        Index("ix_feedback_items_submitter_status", "submitter_user_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    type: Mapped[FeedbackType] = mapped_column(Enum(FeedbackType, name="feedback_type"), nullable=False)
    status: Mapped[FeedbackStatus] = mapped_column(
        Enum(FeedbackStatus, name="feedback_status"),
        nullable=False,
        default=FeedbackStatus.NEW,
        server_default=text("'NEW'"),
    )
    submitter_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    moderator_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reward_points: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    queue_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    queue_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    priority_boost_points_spent: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    priority_boosted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    github_issue_url: Mapped[str | None] = mapped_column(Text, nullable=True)


class TradeFeedback(Base, TimestampMixin):
    __tablename__ = "trade_feedback"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="trade_feedback_rating_range"),
        CheckConstraint("status IN ('VISIBLE', 'HIDDEN')", name="trade_feedback_status_values"),
        UniqueConstraint("auction_id", "author_user_id", name="uq_trade_feedback_auction_author"),
        CheckConstraint("author_user_id <> target_user_id", name="trade_feedback_distinct_users"),
        Index("ix_trade_feedback_status_created_at", "status", "created_at"),
        Index("ix_trade_feedback_target_created_at", "target_user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    auction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auctions.id", ondelete="CASCADE"),
        nullable=False,
    )
    author_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'VISIBLE'"), index=True)
    moderator_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    moderation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GuarantorRequest(Base, TimestampMixin):
    __tablename__ = "guarantor_requests"
    __table_args__ = (
        Index("ix_guarantor_requests_status_created_at", "status", "created_at"),
        Index("ix_guarantor_requests_submitter_status", "submitter_user_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    status: Mapped[GuarantorRequestStatus] = mapped_column(
        Enum(GuarantorRequestStatus, name="guarantor_request_status"),
        nullable=False,
        default=GuarantorRequestStatus.NEW,
        server_default=text("'NEW'"),
    )
    submitter_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    moderator_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    details: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    queue_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    queue_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IntegrationOutbox(Base, TimestampMixin):
    __tablename__ = "integration_outbox"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_integration_outbox_dedupe_key"),
        Index("ix_integration_outbox_status_next_retry_at", "status", "next_retry_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    next_retry_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("TIMEZONE('utc', NOW())"),
    )
    status: Mapped[IntegrationOutboxStatus] = mapped_column(
        Enum(
            IntegrationOutboxStatus,
            name="integration_outbox_status",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=IntegrationOutboxStatus.PENDING,
        server_default=text("'pending'"),
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class PointsLedgerEntry(Base, TimestampMixin):
    __tablename__ = "points_ledger"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="points_ledger_amount_nonzero"),
        UniqueConstraint("dedupe_key", name="uq_points_ledger_dedupe_key"),
        Index("ix_points_ledger_user_created_at", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[PointsEventType] = mapped_column(
        Enum(PointsEventType, name="points_event_type"),
        nullable=False,
    )
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class FraudSignal(Base):
    __tablename__ = "fraud_signals"
    __table_args__ = (
        CheckConstraint("score >= 1", name="fraud_signals_score_positive"),
        CheckConstraint("status IN ('OPEN', 'CONFIRMED', 'DISMISSED')", name="fraud_signals_status_values"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    auction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auctions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bid_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bids.id", ondelete="SET NULL"), nullable=True
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    reasons: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'OPEN'"), index=True)
    queue_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    queue_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resolved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("TIMEZONE('utc', NOW())"), nullable=False, index=True
    )
