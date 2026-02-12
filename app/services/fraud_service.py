from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.enums import AuctionStatus
from app.db.models import Auction, Bid, FraudSignal, User


@dataclass(slots=True)
class FraudSignalView:
    signal: FraudSignal
    auction: Auction
    user: User
    bid: Bid | None
    resolver_user: User | None


async def evaluate_and_store_bid_fraud_signal(
    session: AsyncSession,
    *,
    auction_id: uuid.UUID,
    user_id: int,
    bid_id: uuid.UUID,
) -> int | None:
    auction = await session.scalar(select(Auction).where(Auction.id == auction_id))
    user = await session.scalar(select(User).where(User.id == user_id))
    bid = await session.scalar(select(Bid).where(Bid.id == bid_id))
    if auction is None or user is None or bid is None:
        return None

    now = datetime.now(UTC)
    reasons: list[dict[str, str | int | float]] = []
    score = 0

    rapid_window_start = now - timedelta(seconds=settings.fraud_rapid_window_seconds)
    rapid_count = (
        await session.scalar(
            select(func.count(Bid.id)).where(
                Bid.auction_id == auction_id,
                Bid.user_id == user_id,
                Bid.is_removed.is_(False),
                Bid.created_at >= rapid_window_start,
            )
        )
    ) or 0

    if rapid_count >= settings.fraud_rapid_min_bids:
        rapid_score = min(45, 20 + (rapid_count - settings.fraud_rapid_min_bids + 1) * 5)
        score += int(rapid_score)
        reasons.append(
            {
                "code": "RAPID_BIDDING",
                "detail": f"{rapid_count} ставок за {settings.fraud_rapid_window_seconds} сек",
                "score": int(rapid_score),
            }
        )

    dom_window_start = now - timedelta(seconds=settings.fraud_dominance_window_seconds)
    dom_total = (
        await session.scalar(
            select(func.count(Bid.id)).where(
                Bid.auction_id == auction_id,
                Bid.is_removed.is_(False),
                Bid.created_at >= dom_window_start,
            )
        )
    ) or 0
    dom_user = (
        await session.scalar(
            select(func.count(Bid.id)).where(
                Bid.auction_id == auction_id,
                Bid.user_id == user_id,
                Bid.is_removed.is_(False),
                Bid.created_at >= dom_window_start,
            )
        )
    ) or 0

    if dom_total >= settings.fraud_dominance_min_total_bids:
        ratio = (dom_user / dom_total) if dom_total else 0.0
        if ratio >= settings.fraud_dominance_ratio:
            dom_score = 30
            score += dom_score
            reasons.append(
                {
                    "code": "DOMINANT_BIDDER",
                    "detail": f"доля {ratio:.2f} за {settings.fraud_dominance_window_seconds} сек",
                    "score": dom_score,
                }
            )

    if user.created_at >= now - timedelta(hours=24) and bid.amount >= max(auction.start_price * 3, 150):
        newbie_score = 20
        score += newbie_score
        reasons.append(
            {
                "code": "NEW_ACCOUNT_HIGH_BID",
                "detail": "новый аккаунт + высокая ставка",
                "score": newbie_score,
            }
        )

    duopoly_window_start = now - timedelta(seconds=settings.fraud_duopoly_window_seconds)
    recent_bids = (
        await session.execute(
            select(Bid.user_id, Bid.amount, Bid.created_at)
            .where(
                Bid.auction_id == auction_id,
                Bid.is_removed.is_(False),
                Bid.created_at >= duopoly_window_start,
            )
            .order_by(Bid.created_at.desc())
            .limit(40)
        )
    ).all()

    if len(recent_bids) >= settings.fraud_duopoly_min_total_bids:
        counts = Counter(row.user_id for row in recent_bids)
        top_two = counts.most_common(2)
        if len(top_two) == 2:
            top_user_ids = {top_two[0][0], top_two[1][0]}
            pair_ratio = (top_two[0][1] + top_two[1][1]) / len(recent_bids)
            if user_id in top_user_ids and pair_ratio >= settings.fraud_duopoly_pair_ratio:
                duopoly_score = 25
                score += duopoly_score
                reasons.append(
                    {
                        "code": "DUOPOLY_PATTERN",
                        "detail": f"2 пользователя дали {pair_ratio:.2f} ставок за окно",
                        "score": duopoly_score,
                    }
                )

    recent_ordered = list(reversed(recent_bids[: settings.fraud_alternating_recent_bids]))
    if len(recent_ordered) >= 4:
        users_in_chain = [row.user_id for row in recent_ordered]
        unique_users = set(users_in_chain)
        switches = sum(1 for idx in range(1, len(users_in_chain)) if users_in_chain[idx] != users_in_chain[idx - 1])
        if (
            len(unique_users) == 2
            and user_id in unique_users
            and switches >= settings.fraud_alternating_min_switches
        ):
            alt_score = 20
            score += alt_score
            reasons.append(
                {
                    "code": "ALTERNATING_PAIR",
                    "detail": f"цепочка из 2 пользователей, переключений: {switches}",
                    "score": alt_score,
                }
            )

    previous_bid_amount = await session.scalar(
        select(Bid.amount)
        .where(
            Bid.auction_id == auction_id,
            Bid.is_removed.is_(False),
            Bid.id != bid_id,
        )
        .order_by(Bid.created_at.desc())
        .limit(1)
    )
    if previous_bid_amount is None:
        current_increment = max(bid.amount - auction.start_price, 0)
    else:
        current_increment = max(bid.amount - int(previous_bid_amount), 0)

    baseline_window_start = now - timedelta(seconds=settings.fraud_baseline_window_seconds)
    baseline_rows = (
        await session.execute(
            select(Bid.amount)
            .where(
                Bid.auction_id == auction_id,
                Bid.is_removed.is_(False),
                Bid.created_at >= baseline_window_start,
            )
            .order_by(Bid.created_at.asc())
            .limit(80)
        )
    ).all()
    baseline_amounts = [int(row.amount) for row in baseline_rows]

    if len(baseline_amounts) >= settings.fraud_baseline_min_bids:
        increments = [
            max(baseline_amounts[idx] - baseline_amounts[idx - 1], 0)
            for idx in range(1, len(baseline_amounts))
        ]
        if len(increments) >= max(settings.fraud_baseline_min_bids - 1, 1):
            historical_increments = [value for value in increments[:-1] if value > 0]
            if historical_increments:
                med_increment = float(median(historical_increments))
                threshold = max(
                    settings.fraud_baseline_min_increment,
                    int(med_increment * settings.fraud_baseline_spike_factor),
                )
                if current_increment >= threshold:
                    score += settings.fraud_baseline_spike_score
                    reasons.append(
                        {
                            "code": "BASELINE_SPIKE",
                            "detail": (
                                f"скачок +{current_increment}, медиана {med_increment:.1f}, "
                                f"порог {threshold}"
                            ),
                            "score": settings.fraud_baseline_spike_score,
                        }
                    )

    historical_start_min = max(1, int(auction.start_price * settings.fraud_historical_start_ratio_low))
    historical_start_max = max(
        historical_start_min,
        int(auction.start_price * settings.fraud_historical_start_ratio_high),
    )

    historical_auction_ids = (
        await session.execute(
            select(Auction.id)
            .where(
                Auction.id != auction_id,
                Auction.status.in_([AuctionStatus.ENDED, AuctionStatus.BOUGHT_OUT]),
                Auction.start_price >= historical_start_min,
                Auction.start_price <= historical_start_max,
            )
            .order_by(Auction.ends_at.desc().nullslast(), Auction.updated_at.desc())
            .limit(settings.fraud_historical_completed_auctions)
        )
    ).scalars().all()

    if historical_auction_ids:
        historical_rows = (
            await session.execute(
                select(Bid.auction_id, Bid.amount, Auction.start_price)
                .join(Auction, Auction.id == Bid.auction_id)
                .where(
                    Bid.auction_id.in_(historical_auction_ids),
                    Bid.is_removed.is_(False),
                )
                .order_by(Bid.auction_id.asc(), Bid.created_at.asc())
            )
        ).all()

        by_auction: dict[uuid.UUID, list[int]] = {}
        start_prices: dict[uuid.UUID, int] = {}
        for row in historical_rows:
            by_auction.setdefault(row.auction_id, []).append(int(row.amount))
            start_prices[row.auction_id] = int(row.start_price)

        historical_increments: list[int] = []
        for hist_auction_id, amounts in by_auction.items():
            if len(amounts) < 2:
                continue
            prev = start_prices.get(hist_auction_id, amounts[0])
            for amount in amounts:
                increment = max(amount - prev, 0)
                if increment > 0:
                    historical_increments.append(increment)
                prev = amount

        if len(historical_increments) >= settings.fraud_historical_min_points:
            historical_median = float(median(historical_increments))
            historical_threshold = max(
                settings.fraud_historical_min_increment,
                int(historical_median * settings.fraud_historical_spike_factor),
            )
            if current_increment >= historical_threshold:
                score += settings.fraud_historical_spike_score
                reasons.append(
                    {
                        "code": "HISTORICAL_BASELINE_SPIKE",
                        "detail": (
                            f"+{current_increment} vs hist median {historical_median:.1f}, "
                            f"порог {historical_threshold}, выборка {len(historical_increments)}"
                        ),
                        "score": settings.fraud_historical_spike_score,
                    }
                )

    if score < settings.fraud_alert_threshold:
        return None

    duplicate_open = await session.scalar(
        select(FraudSignal.id).where(
            FraudSignal.auction_id == auction_id,
            FraudSignal.user_id == user_id,
            FraudSignal.status == "OPEN",
            FraudSignal.created_at >= now - timedelta(minutes=10),
        )
    )
    if duplicate_open is not None:
        return None

    signal = FraudSignal(
        auction_id=auction_id,
        user_id=user_id,
        bid_id=bid_id,
        score=score,
        reasons={"rules": reasons},
        status="OPEN",
    )
    session.add(signal)
    await session.flush()
    return signal.id


async def load_fraud_signal_view(
    session: AsyncSession,
    signal_id: int,
    *,
    for_update: bool = False,
) -> FraudSignalView | None:
    stmt = select(FraudSignal).where(FraudSignal.id == signal_id)
    if for_update:
        stmt = stmt.with_for_update()
    signal = await session.scalar(stmt)
    if signal is None:
        return None

    auction = await session.scalar(select(Auction).where(Auction.id == signal.auction_id))
    user = await session.scalar(select(User).where(User.id == signal.user_id))
    if auction is None or user is None:
        return None

    bid = None
    if signal.bid_id is not None:
        bid = await session.scalar(select(Bid).where(Bid.id == signal.bid_id))

    resolver_user = None
    if signal.resolved_by_user_id is not None:
        resolver_user = await session.scalar(select(User).where(User.id == signal.resolved_by_user_id))

    return FraudSignalView(
        signal=signal,
        auction=auction,
        user=user,
        bid=bid,
        resolver_user=resolver_user,
    )


def render_fraud_signal_text(view: FraudSignalView) -> str:
    actor = f"@{view.user.username}" if view.user.username else str(view.user.tg_user_id)
    bid_part = "-"
    if view.bid is not None:
        bid_part = f"{view.bid.id} (${view.bid.amount})"

    reasons = view.signal.reasons.get("rules", []) if isinstance(view.signal.reasons, dict) else []
    lines = [
        f"Фрод-сигнал #{view.signal.id}",
        f"Статус: {view.signal.status}",
        f"Аукцион: {view.auction.id}",
        f"Пользователь: {actor}",
        f"Ставка: {bid_part}",
        f"Риск-скор: {view.signal.score}",
        "Причины:",
    ]
    if not reasons:
        lines.append("- нет")
    else:
        for reason in reasons:
            lines.append(f"- {reason.get('code')}: {reason.get('detail')} (+{reason.get('score')})")
    if view.signal.resolution_note:
        lines.append(f"Решение: {view.signal.resolution_note}")
    if view.resolver_user is not None:
        resolver = (
            f"@{view.resolver_user.username}"
            if view.resolver_user.username
            else str(view.resolver_user.tg_user_id)
        )
        lines.append(f"Модератор: {resolver}")
    return "\n".join(lines)


async def set_fraud_signal_queue_message(
    session: AsyncSession,
    *,
    signal_id: int,
    chat_id: int,
    message_id: int,
) -> None:
    signal = await session.scalar(select(FraudSignal).where(FraudSignal.id == signal_id).with_for_update())
    if signal is None:
        return
    signal.queue_chat_id = chat_id
    signal.queue_message_id = message_id


async def resolve_fraud_signal(
    session: AsyncSession,
    *,
    signal_id: int,
    resolver_user_id: int,
    status: str,
    note: str,
) -> FraudSignal | None:
    signal = await session.scalar(select(FraudSignal).where(FraudSignal.id == signal_id).with_for_update())
    if signal is None:
        return None
    signal.status = status
    signal.resolved_by_user_id = resolver_user_id
    signal.resolution_note = note
    signal.resolved_at = datetime.now(UTC)
    return signal


async def list_fraud_signals(
    session: AsyncSession,
    *,
    auction_id: uuid.UUID | None,
    status: str | None,
    limit: int = 20,
    offset: int = 0,
) -> list[FraudSignal]:
    stmt = (
        select(FraudSignal)
        .order_by(FraudSignal.created_at.desc())
        .offset(max(offset, 0))
        .limit(max(limit, 1))
    )
    if auction_id is not None:
        stmt = stmt.where(FraudSignal.auction_id == auction_id)
    if status is not None:
        stmt = stmt.where(FraudSignal.status == status)
    return list((await session.execute(stmt)).scalars().all())


async def has_open_signal_for_bid(session: AsyncSession, bid_id: uuid.UUID) -> bool:
    return (
        await session.scalar(
            select(FraudSignal.id).where(
                and_(FraudSignal.bid_id == bid_id, FraudSignal.status == "OPEN")
            )
        )
    ) is not None
