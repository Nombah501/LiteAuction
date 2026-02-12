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
