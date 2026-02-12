from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from app.config import settings
from app.services.appeal_escalation_service import process_overdue_appeal_escalations

logger = logging.getLogger(__name__)


async def run_appeal_escalation_watcher(bot: Bot) -> None:
    interval = max(settings.appeal_escalation_interval_seconds, 1)
    while True:
        try:
            escalated_count = await process_overdue_appeal_escalations(bot)
            if escalated_count:
                logger.warning("Appeal escalation watcher escalated %s appeal(s)", escalated_count)
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Appeal escalation watcher failed: %s", exc)
            await asyncio.sleep(interval)
