from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot

from app.config import settings
from app.services.auction_service import finalize_expired_auctions

logger = logging.getLogger(__name__)


async def run_auction_watcher(bot: Bot) -> None:
    interval = max(settings.auction_watcher_interval_seconds, 1)
    while True:
        try:
            closed = await finalize_expired_auctions(bot)
            if closed:
                logger.info("Auction watcher finalized %s auction(s)", closed)
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Auction watcher failed: %s", exc)
            await asyncio.sleep(interval)


async def cancel_watcher(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
