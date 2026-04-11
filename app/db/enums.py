from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MODERATOR = "MODERATOR"
    SELLER = "SELLER"
    BIDDER = "BIDDER"


class AuctionStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ENDED = "ENDED"
    BOUGHT_OUT = "BOUGHT_OUT"
    CANCELLED = "CANCELLED"
    FROZEN = "FROZEN"


class ModerationAction(StrEnum):
    FREEZE_AUCTION = "FREEZE_AUCTION"
    UNFREEZE_AUCTION = "UNFREEZE_AUCTION"
    END_AUCTION = "END_AUCTION"
    REMOVE_BID = "REMOVE_BID"
    BAN_USER = "BAN_USER"
    UNBAN_USER = "UNBAN_USER"
    RESOLVE_APPEAL = "RESOLVE_APPEAL"
    REJECT_APPEAL = "REJECT_APPEAL"
    ESCALATE_APPEAL = "ESCALATE_APPEAL"
    TAKE_FEEDBACK = "TAKE_FEEDBACK"
    APPROVE_FEEDBACK = "APPROVE_FEEDBACK"
    REJECT_FEEDBACK = "REJECT_FEEDBACK"
    CREATE_FEEDBACK_GITHUB_ISSUE = "CREATE_FEEDBACK_GITHUB_ISSUE"
    ASSIGN_GUARANTOR_REQUEST = "ASSIGN_GUARANTOR_REQUEST"
    REJECT_GUARANTOR_REQUEST = "REJECT_GUARANTOR_REQUEST"
    ADJUST_USER_POINTS = "ADJUST_USER_POINTS"
    HIDE_TRADE_FEEDBACK = "HIDE_TRADE_FEEDBACK"
    UNHIDE_TRADE_FEEDBACK = "UNHIDE_TRADE_FEEDBACK"
    SET_BOT_PROFILE_PHOTO = "SET_BOT_PROFILE_PHOTO"
    REMOVE_BOT_PROFILE_PHOTO = "REMOVE_BOT_PROFILE_PHOTO"
    UPDATE_MODERATION_CHECKLIST = "UPDATE_MODERATION_CHECKLIST"


class AppealSourceType(StrEnum):
    COMPLAINT = "complaint"
    RISK = "risk"
    MANUAL = "manual"


class AppealStatus(StrEnum):
    OPEN = "OPEN"
    IN_REVIEW = "IN_REVIEW"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"


class FeedbackType(StrEnum):
    BUG = "BUG"
    SUGGESTION = "SUGGESTION"


class FeedbackStatus(StrEnum):
    NEW = "NEW"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class IntegrationOutboxStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class GuarantorRequestStatus(StrEnum):
    NEW = "NEW"
    ASSIGNED = "ASSIGNED"
    REJECTED = "REJECTED"


class DealTopicStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class PointsEventType(StrEnum):
    FEEDBACK_APPROVED = "FEEDBACK_APPROVED"
    MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"
    FEEDBACK_PRIORITY_BOOST = "FEEDBACK_PRIORITY_BOOST"
    GUARANTOR_PRIORITY_BOOST = "GUARANTOR_PRIORITY_BOOST"
    APPEAL_PRIORITY_BOOST = "APPEAL_PRIORITY_BOOST"


class ReputationTier(StrEnum):
    NEW = "NEW"
    BRONZE = "BRONZE"
    SILVER = "SILVER"
    GOLD = "GOLD"
    PLATINUM = "PLATINUM"


class ReputationEventReason(StrEnum):
    AUCTION_COMPLETED = "auction_completed"
    BID_WON = "bid_won"
    GUARANTOR_ASSIGNED = "guarantor_assigned"
    USER_VERIFIED = "user_verified"
    COMPLAINT_FILED = "complaint_filed"
    FRAUD_SIGNAL = "fraud_signal"
    FRAUD_CONFIRMED = "fraud_confirmed"
    BID_REMOVED = "bid_removed"
    TEMP_BAN = "temp_ban"
    PERM_BAN = "perm_ban"
    MOD_ADJUST = "mod_adjust"
    DECAY = "decay"
