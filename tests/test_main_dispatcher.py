from __future__ import annotations

import pytest
from aiogram.fsm.storage.redis import RedisEventIsolation, RedisStorage

from app.main import build_dispatcher


@pytest.mark.asyncio
async def test_build_dispatcher_uses_redis_storage_and_event_isolation() -> None:
    dp = build_dispatcher()

    try:
        assert isinstance(dp.storage, RedisStorage)
        assert isinstance(dp.fsm.events_isolation, RedisEventIsolation)
    finally:
        await dp.fsm.close()
