from __future__ import annotations

import uuid

from app.config import settings
from app.infra.redis_client import redis_client


def _cooldown_key(auction_id: uuid.UUID, user_id: int) -> str:
    return f"bid:cooldown:{auction_id}:{user_id}"


def _confirmation_key(auction_id: uuid.UUID, user_id: int, action: str) -> str:
    return f"bid:confirm:{auction_id}:{user_id}:{action}"


def _complaint_cooldown_key(auction_id: uuid.UUID, user_id: int) -> str:
    return f"complaint:cooldown:{auction_id}:{user_id}"


async def acquire_bid_cooldown(auction_id: uuid.UUID, user_id: int) -> bool:
    ttl = max(settings.bid_cooldown_seconds, 1)
    result = await redis_client.set(_cooldown_key(auction_id, user_id), "1", ex=ttl, nx=True)
    return bool(result)


async def arm_or_confirm_action(auction_id: uuid.UUID, user_id: int, action: str) -> bool:
    key = _confirmation_key(auction_id, user_id, action)
    exists = await redis_client.get(key)
    if exists is not None:
        await redis_client.delete(key)
        return False

    ttl = max(settings.confirmation_ttl_seconds, 1)
    await redis_client.set(key, "1", ex=ttl)
    return True


async def acquire_complaint_cooldown(auction_id: uuid.UUID, user_id: int) -> bool:
    ttl = max(settings.complaint_cooldown_seconds, 1)
    result = await redis_client.set(_complaint_cooldown_key(auction_id, user_id), "1", ex=ttl, nx=True)
    return bool(result)
