from __future__ import annotations

from enum import StrEnum

from app.config import settings


class AuctionMessageEffectEvent(StrEnum):
    OUTBID = "outbid"
    BUYOUT_SELLER = "buyout_seller"
    BUYOUT_WINNER = "buyout_winner"
    ENDED_SELLER = "ended_seller"
    ENDED_WINNER = "ended_winner"


def resolve_auction_message_effect_id(event: AuctionMessageEffectEvent | str) -> str | None:
    if not settings.auction_message_effects_enabled:
        return None
    return settings.parsed_auction_effect_id(str(event))
