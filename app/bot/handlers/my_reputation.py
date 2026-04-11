from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.filters import Command
from sqlalchemy import select, func

from app.db.models import UserReputation, UserReputationEvent, TradeFeedback, User, Auction
from app.db.enums import ReputationTier, AuctionStatus
from app.db.session import SessionFactory

router = Router(name="my_reputation")

TIER_THRESHOLDS: dict[ReputationTier, int] = {
    ReputationTier.NEW: 0,
    ReputationTier.BRONZE: 100,
    ReputationTier.SILVER: 500,
    ReputationTier.GOLD: 1000,
    ReputationTier.PLATINUM: 3000,
}

TIER_ORDER = [
    ReputationTier.NEW,
    ReputationTier.BRONZE,
    ReputationTier.SILVER,
    ReputationTier.GOLD,
    ReputationTier.PLATINUM,
]


def format_tier_badge(tier: ReputationTier) -> str:
    badges = {
        ReputationTier.NEW: "",
        ReputationTier.BRONZE: "🥉 BRONZE",
        ReputationTier.SILVER: "🥈 SILVER",
        ReputationTier.GOLD: "🥇 GOLD",
        ReputationTier.PLATINUM: "💎 PLATINUM",
    }
    return badges.get(tier, "")


def build_reputation_text(
    *,
    tier: ReputationTier,
    score: int,
    deals_count: int,
    feedback_received: int,
    avg_rating: float | None,
    feedback_given: int,
    next_tier: ReputationTier | None,
    next_tier_threshold: int | None,
) -> str:
    badge = format_tier_badge(tier)
    tier_display = badge if badge else tier.value

    lines = [
        f"⭐ <b>Ваша репутация: {tier_display}</b>",
        f"Баллы: <b>{score}</b>",
        "",
        f"Завершённых сделок: {deals_count}",
        f"Отзывов получено: {feedback_received}",
    ]

    if avg_rating is not None:
        lines.append(f"Средняя оценка: {avg_rating:.1f}⭐")

    lines.append(f"Отзывов оставлено: {feedback_given}")

    if next_tier is not None and next_tier_threshold is not None:
        remaining = next_tier_threshold - score
        lines.extend([
            "",
            f"Следующий tier: {format_tier_badge(next_tier)} ({next_tier_threshold} баллов)",
            f"Осталось: {remaining}",
        ])
    elif tier == ReputationTier.PLATINUM:
        lines.extend(["", "🏆 Максимальный tier достигнут!"])

    return "\n".join(lines)


@router.message(Command("myrep"))
async def handle_myrep_command(message: Message) -> None:
    if message.from_user is None:
        return

    async with SessionFactory() as session:
        user = await session.scalar(
            select(User).where(User.tg_user_id == message.from_user.id)
        )
        if user is None:
            await message.answer("Сначала откройте бота через /start")
            return

        text = await _build_myrep_text(session, user.id)

    await message.answer(text, parse_mode="HTML")


@router.callback_query(F.data == "dash:reputation")
async def handle_myrep_callback(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.message is None:
        return

    async with SessionFactory() as session:
        user = await session.scalar(
            select(User).where(User.tg_user_id == callback.from_user.id)
        )
        if user is None:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        text = await _build_myrep_text(session, user.id)

    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()


async def _build_myrep_text(session, user_id: int) -> str:
    rep = await session.scalar(
        select(UserReputation).where(UserReputation.user_id == user_id)
    )

    if rep is None:
        return (
            "⭐ <b>Ваша репутация: NEW</b>\n"
            "Баллы: 0\n\n"
            "Пока нет данных. Совершайте сделки и оставляйте отзывы!"
        )

    finished_statuses = {AuctionStatus.ENDED, AuctionStatus.BOUGHT_OUT}
    deals_count = await session.scalar(
        select(func.count())
        .select_from(Auction)
        .where(
            Auction.winner_user_id == user_id,
            Auction.status.in_(finished_statuses),
        )
    ) or 0

    feedback_received = await session.scalar(
        select(func.count())
        .select_from(TradeFeedback)
        .where(TradeFeedback.target_user_id == user_id)
    ) or 0

    avg_rating = await session.scalar(
        select(func.avg(TradeFeedback.rating))
        .where(TradeFeedback.target_user_id == user_id)
    )

    feedback_given = await session.scalar(
        select(func.count())
        .select_from(TradeFeedback)
        .where(TradeFeedback.author_user_id == user_id)
    ) or 0

    current_tier = ReputationTier(rep.tier)
    current_idx = TIER_ORDER.index(current_tier)
    next_tier = TIER_ORDER[current_idx + 1] if current_idx + 1 < len(TIER_ORDER) else None
    next_threshold = TIER_THRESHOLDS.get(next_tier) if next_tier else None

    recent_events = (await session.execute(
        select(UserReputationEvent)
        .where(UserReputationEvent.user_id == user_id)
        .order_by(UserReputationEvent.created_at.desc())
        .limit(5)
    )).scalars().all()

    text = build_reputation_text(
        tier=current_tier,
        score=rep.score,
        deals_count=deals_count,
        feedback_received=feedback_received,
        avg_rating=float(avg_rating) if avg_rating else None,
        feedback_given=feedback_given,
        next_tier=next_tier,
        next_tier_threshold=next_threshold,
    )

    if recent_events:
        text += "\n\n📊 <b>Последние события:</b>"
        for event in recent_events:
            delta_str = f"+{event.delta}" if event.delta > 0 else str(event.delta)
            text += f"\n{delta_str} — {event.reason}"

    return text
