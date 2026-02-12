from __future__ import annotations

from redis.asyncio import Redis

from app.config import settings

redis_client: Redis = Redis.from_url(settings.redis_url, decode_responses=True)


async def ping_redis() -> None:
    await redis_client.ping()


async def close_redis() -> None:
    await redis_client.close()
