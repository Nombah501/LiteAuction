from __future__ import annotations

import asyncio

from app.db.session import ping_database
from app.infra.redis_client import close_redis, ping_redis


async def check() -> int:
    try:
        await ping_database()
        await ping_redis()
        await close_redis()
        return 0
    except Exception:
        return 1


def main() -> None:
    raise SystemExit(asyncio.run(check()))


if __name__ == "__main__":
    main()
