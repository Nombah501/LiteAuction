from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from app.config import settings
from app.infra.redis_client import redis_client

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OutbidDigestDecision:
    suppressed_count: int
    window_seconds: int
    should_emit_digest: bool


def _outbid_digest_count_key(*, tg_user_id: int, auction_id: uuid.UUID) -> str:
    return f"notif:digest:outbid:{tg_user_id}:{auction_id}:count"


def _outbid_digest_emit_key(*, tg_user_id: int, auction_id: uuid.UUID) -> str:
    return f"notif:digest:outbid:{tg_user_id}:{auction_id}:emit"


async def register_outbid_notification_suppression(
    *,
    tg_user_id: int,
    auction_id: uuid.UUID,
) -> OutbidDigestDecision:
    window_seconds = max(settings.outbid_notification_digest_window_seconds, 1)
    count_key = _outbid_digest_count_key(tg_user_id=tg_user_id, auction_id=auction_id)
    emit_key = _outbid_digest_emit_key(tg_user_id=tg_user_id, auction_id=auction_id)

    try:
        suppressed_count = int(await redis_client.incr(count_key))
        if suppressed_count == 1:
            await redis_client.expire(count_key, window_seconds)

        should_emit_digest = False
        if suppressed_count >= 2:
            should_emit_digest = bool(
                await redis_client.set(emit_key, "1", ex=window_seconds, nx=True)
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "notification_digest_register_failed tg_user_id=%s auction_id=%s error=%s",
            tg_user_id,
            auction_id,
            exc,
        )
        return OutbidDigestDecision(
            suppressed_count=0,
            window_seconds=window_seconds,
            should_emit_digest=False,
        )

    return OutbidDigestDecision(
        suppressed_count=suppressed_count,
        window_seconds=window_seconds,
        should_emit_digest=should_emit_digest,
    )
