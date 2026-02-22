from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from enum import StrEnum

from app.infra.redis_client import redis_client

logger = logging.getLogger(__name__)
_METRIC_SCAN_MATCH = "bot:funnel:*"


class BotFunnelJourney(StrEnum):
    AUCTION_CREATE = "auction_create"
    BID = "bid"
    BUYOUT = "buyout"
    COMPLAINT = "complaint"
    BOOST_FEEDBACK = "boost_feedback"
    BOOST_GUARANTOR = "boost_guarantor"
    BOOST_APPEAL = "boost_appeal"


class BotFunnelStep(StrEnum):
    START = "start"
    COMPLETE = "complete"
    FAIL = "fail"


class BotFunnelActorRole(StrEnum):
    USER = "user"
    SELLER = "seller"
    BIDDER = "bidder"


@dataclass(slots=True, frozen=True)
class BotFunnelDropOff:
    journey: BotFunnelJourney
    reason: str
    context_key: str
    actor_role: BotFunnelActorRole
    total: int


@dataclass(slots=True, frozen=True)
class BotFunnelJourneySnapshot:
    journey: BotFunnelJourney
    starts: int
    completes: int
    fails: int
    conversion_rate_percent: float
    top_drop_offs: tuple[BotFunnelDropOff, ...]


@dataclass(slots=True, frozen=True)
class BotFunnelSnapshot:
    journey_summaries: tuple[BotFunnelJourneySnapshot, ...]
    top_drop_offs: tuple[BotFunnelDropOff, ...]
    total_starts: int
    total_completes: int
    total_fails: int


def _normalize_segment(value: str | None, *, fallback: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return fallback
    normalized = re.sub(r"[^a-z0-9_-]+", "_", raw)
    normalized = normalized.strip("_")
    return normalized or fallback


def _metric_key(
    *,
    journey: BotFunnelJourney,
    step: BotFunnelStep,
    actor_role: BotFunnelActorRole,
    context_key: str,
    reason: str,
) -> str:
    return (
        f"bot:funnel:{journey.value}:{step.value}:{actor_role.value}:"
        f"{_normalize_segment(context_key, fallback='unknown')}:{_normalize_segment(reason, fallback='unknown')}"
    )


def _parse_metric_key(
    key: str,
) -> tuple[BotFunnelJourney, BotFunnelStep, BotFunnelActorRole, str, str] | None:
    parts = key.split(":", 6)
    if len(parts) != 7:
        return None
    if parts[0] != "bot" or parts[1] != "funnel":
        return None
    try:
        journey = BotFunnelJourney(parts[2])
        step = BotFunnelStep(parts[3])
        actor_role = BotFunnelActorRole(parts[4])
    except ValueError:
        return None

    context_key = _normalize_segment(parts[5], fallback="unknown")
    reason = _normalize_segment(parts[6], fallback="unknown")
    return journey, step, actor_role, context_key, reason


async def record_bot_funnel_event(
    *,
    journey: BotFunnelJourney,
    step: BotFunnelStep,
    actor_role: BotFunnelActorRole,
    context_key: str,
    failure_reason: str | None = None,
    count: int = 1,
) -> int | None:
    normalized_count = max(int(count), 1)
    normalized_context = _normalize_segment(context_key, fallback="unknown")
    normalized_reason = (
        _normalize_segment(failure_reason, fallback="unknown") if step == BotFunnelStep.FAIL else "ok"
    )
    key = _metric_key(
        journey=journey,
        step=step,
        actor_role=actor_role,
        context_key=normalized_context,
        reason=normalized_reason,
    )

    try:
        total = await redis_client.incrby(key, normalized_count)
    except Exception:  # pragma: no cover - defensive safety around redis runtime
        logger.warning(
            "bot_funnel_metric_failed journey=%s step=%s actor=%s context=%s reason=%s",
            journey.value,
            step.value,
            actor_role.value,
            normalized_context,
            normalized_reason,
            exc_info=True,
        )
        return None
    return int(total)


async def _scan_metric_keys() -> list[str]:
    cursor: int | str = 0
    keys: list[str] = []
    while True:
        cursor, batch = await redis_client.scan(cursor=cursor, match=_METRIC_SCAN_MATCH, count=500)
        keys.extend(batch)
        if int(cursor) == 0:
            break
    return keys


async def load_bot_funnel_snapshot(*, top_limit: int = 5) -> BotFunnelSnapshot:
    normalized_top_limit = max(int(top_limit), 1)
    try:
        keys = await _scan_metric_keys()
    except Exception:  # pragma: no cover - defensive safety around redis runtime
        logger.warning("bot_funnel_snapshot_scan_failed", exc_info=True)
        return BotFunnelSnapshot(
            journey_summaries=(),
            top_drop_offs=(),
            total_starts=0,
            total_completes=0,
            total_fails=0,
        )

    if not keys:
        return BotFunnelSnapshot(
            journey_summaries=(),
            top_drop_offs=(),
            total_starts=0,
            total_completes=0,
            total_fails=0,
        )

    try:
        raw_values = await redis_client.mget(keys)
    except Exception:  # pragma: no cover - defensive safety around redis runtime
        logger.warning("bot_funnel_snapshot_mget_failed", exc_info=True)
        return BotFunnelSnapshot(
            journey_summaries=(),
            top_drop_offs=(),
            total_starts=0,
            total_completes=0,
            total_fails=0,
        )

    starts_by_journey: dict[BotFunnelJourney, int] = {}
    completes_by_journey: dict[BotFunnelJourney, int] = {}
    fails_by_journey: dict[BotFunnelJourney, int] = {}
    dropoffs_by_journey: dict[BotFunnelJourney, dict[tuple[str, str, BotFunnelActorRole], int]] = {}
    global_dropoffs: dict[tuple[BotFunnelJourney, str, str, BotFunnelActorRole], int] = {}

    for key, raw_value in zip(keys, raw_values, strict=False):
        parsed = _parse_metric_key(key)
        if parsed is None:
            continue
        try:
            value = int(raw_value or 0)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue

        journey, step, actor_role, context_key, reason = parsed
        if step == BotFunnelStep.START:
            starts_by_journey[journey] = starts_by_journey.get(journey, 0) + value
        elif step == BotFunnelStep.COMPLETE:
            completes_by_journey[journey] = completes_by_journey.get(journey, 0) + value
        else:
            fails_by_journey[journey] = fails_by_journey.get(journey, 0) + value
            journey_dropoffs = dropoffs_by_journey.setdefault(journey, {})
            journey_dropoff_key = (reason, context_key, actor_role)
            journey_dropoffs[journey_dropoff_key] = journey_dropoffs.get(journey_dropoff_key, 0) + value
            global_dropoff_key = (journey, reason, context_key, actor_role)
            global_dropoffs[global_dropoff_key] = global_dropoffs.get(global_dropoff_key, 0) + value

    journeys = sorted(
        set(starts_by_journey) | set(completes_by_journey) | set(fails_by_journey),
        key=lambda value: value.value,
    )
    summaries: list[BotFunnelJourneySnapshot] = []
    for journey in journeys:
        starts = starts_by_journey.get(journey, 0)
        completes = completes_by_journey.get(journey, 0)
        fails = fails_by_journey.get(journey, 0)
        conversion_rate = 0.0 if starts <= 0 else round((completes / starts) * 100.0, 1)
        raw_dropoffs = dropoffs_by_journey.get(journey, {})
        top_drop_offs = tuple(
            BotFunnelDropOff(
                journey=journey,
                reason=reason,
                context_key=context_key,
                actor_role=actor_role,
                total=total,
            )
            for (reason, context_key, actor_role), total in sorted(
                raw_dropoffs.items(),
                key=lambda item: (-item[1], item[0][0], item[0][1], item[0][2].value),
            )[:normalized_top_limit]
        )
        summaries.append(
            BotFunnelJourneySnapshot(
                journey=journey,
                starts=starts,
                completes=completes,
                fails=fails,
                conversion_rate_percent=conversion_rate,
                top_drop_offs=top_drop_offs,
            )
        )

    snapshot_top_drop_offs = tuple(
        BotFunnelDropOff(
            journey=journey,
            reason=reason,
            context_key=context_key,
            actor_role=actor_role,
            total=total,
        )
        for (journey, reason, context_key, actor_role), total in sorted(
            global_dropoffs.items(),
            key=lambda item: (-item[1], item[0][0].value, item[0][1], item[0][2], item[0][3].value),
        )[:normalized_top_limit]
    )

    return BotFunnelSnapshot(
        journey_summaries=tuple(summaries),
        top_drop_offs=snapshot_top_drop_offs,
        total_starts=sum(starts_by_journey.values()),
        total_completes=sum(completes_by_journey.values()),
        total_fails=sum(fails_by_journey.values()),
    )
