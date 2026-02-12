from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.services.outbox_service import process_pending_outbox_events

logger = logging.getLogger(__name__)


async def run_outbox_watcher() -> None:
    interval = max(settings.outbox_watcher_interval_seconds, 1)
    while True:
        try:
            processed = await process_pending_outbox_events()
            if processed:
                logger.info("Outbox watcher processed %s event(s)", processed)
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Outbox watcher failed: %s", exc)
            await asyncio.sleep(interval)
