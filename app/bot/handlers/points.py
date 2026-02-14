from __future__ import annotations

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from app.config import settings
from app.db.enums import PointsEventType
from app.db.models import PointsLedgerEntry
from app.db.session import SessionFactory
from app.services.appeal_service import AppealPriorityBoostPolicy, get_appeal_priority_boost_policy
from app.services.feedback_service import FeedbackPriorityBoostPolicy, get_feedback_priority_boost_policy
from app.services.guarantor_service import GuarantorPriorityBoostPolicy, get_guarantor_priority_boost_policy
from app.services.points_service import (
    UserPointsSummary,
    get_points_redemptions_used_today,
    get_points_redemption_cooldown_remaining_seconds,
    get_user_points_summary,
    list_user_points_entries,
)
from app.services.user_service import upsert_user

router = Router(name="points")
DEFAULT_POINTS_HISTORY_LIMIT = 5
MAX_POINTS_HISTORY_LIMIT = 20


def _event_label(event_type: PointsEventType) -> str:
    if event_type == PointsEventType.FEEDBACK_APPROVED:
        return "Награда за фидбек"
    if event_type == PointsEventType.FEEDBACK_PRIORITY_BOOST:
        return "Списание за приоритет фидбека"
    if event_type == PointsEventType.GUARANTOR_PRIORITY_BOOST:
        return "Списание за приоритет гаранта"
    if event_type == PointsEventType.APPEAL_PRIORITY_BOOST:
        return "Списание за приоритет апелляции"
    return "Ручная корректировка"


def _parse_history_limit(raw: str | None) -> int | None:
    if raw is None:
        return DEFAULT_POINTS_HISTORY_LIMIT
    if not raw.isdigit():
        return None
    value = int(raw)
    if value < 1 or value > MAX_POINTS_HISTORY_LIMIT:
        return None
    return value


def _render_points_text(
    *,
    summary: UserPointsSummary,
    entries: list[PointsLedgerEntry],
    shown_limit: int,
    feedback_boost_policy: FeedbackPriorityBoostPolicy,
    guarantor_boost_policy: GuarantorPriorityBoostPolicy,
    appeal_boost_policy: AppealPriorityBoostPolicy,
    redemptions_used_today: int,
    cooldown_remaining_seconds: int,
) -> str:
    global_daily_limit = max(settings.points_redemption_daily_limit, 0)
    global_remaining_today = max(global_daily_limit - redemptions_used_today, 0)

    lines = [
        f"Ваш баланс: {summary.balance} points",
        f"Всего начислено: +{summary.total_earned}",
        f"Всего списано: -{summary.total_spent}",
        f"Буст фидбека: /boostfeedback <feedback_id> (стоимость: {feedback_boost_policy.cost_points} points)",
        (
            "Лимит фидбек-бустов сегодня: "
            f"{feedback_boost_policy.used_today}/{feedback_boost_policy.daily_limit} "
            f"(осталось {feedback_boost_policy.remaining_today})"
        ),
        f"Статус фидбек-буста: {'доступен' if feedback_boost_policy.enabled else 'временно отключен'}",
        (
            f"Кулдаун фидбек-буста: {feedback_boost_policy.cooldown_seconds} сек "
            f"(осталось {feedback_boost_policy.cooldown_remaining_seconds})"
        ),
        f"Буст гаранта: /boostguarant <request_id> (стоимость: {guarantor_boost_policy.cost_points} points)",
        (
            "Лимит бустов гаранта сегодня: "
            f"{guarantor_boost_policy.used_today}/{guarantor_boost_policy.daily_limit} "
            f"(осталось {guarantor_boost_policy.remaining_today})"
        ),
        f"Статус буста гаранта: {'доступен' if guarantor_boost_policy.enabled else 'временно отключен'}",
        (
            f"Кулдаун буста гаранта: {guarantor_boost_policy.cooldown_seconds} сек "
            f"(осталось {guarantor_boost_policy.cooldown_remaining_seconds})"
        ),
        f"Буст апелляции: /boostappeal <appeal_id> (стоимость: {appeal_boost_policy.cost_points} points)",
        (
            "Лимит бустов апелляций сегодня: "
            f"{appeal_boost_policy.used_today}/{appeal_boost_policy.daily_limit} "
            f"(осталось {appeal_boost_policy.remaining_today})"
        ),
        f"Статус буста апелляции: {'доступен' if appeal_boost_policy.enabled else 'временно отключен'}",
        (
            f"Кулдаун буста апелляции: {appeal_boost_policy.cooldown_seconds} сек "
            f"(осталось {appeal_boost_policy.cooldown_remaining_seconds})"
        ),
        (
            f"Глобальный лимит бустов в день: {redemptions_used_today}/{global_daily_limit} "
            f"(осталось {global_remaining_today})"
            if global_daily_limit > 0
            else "Глобальный лимит бустов в день: без ограничений"
        ),
        f"Глобальный кулдаун между бустами: {max(settings.points_redemption_cooldown_seconds, 0)} сек",
        (
            f"До следующего буста: {cooldown_remaining_seconds} сек"
            if cooldown_remaining_seconds > 0
            else "Следующий буст доступен сейчас"
        ),
    ]
    if not entries:
        lines.append("Начислений пока нет")
        return "\n".join(lines)

    lines.append("")
    lines.append(f"Последние операции (до {shown_limit}):")
    for entry in entries:
        created_at = entry.created_at.astimezone().strftime("%d.%m %H:%M")
        amount_text = f"+{entry.amount}" if entry.amount > 0 else str(entry.amount)
        lines.append(f"- {created_at} | {amount_text} | {_event_label(PointsEventType(entry.event_type))}")
    return "\n".join(lines)


@router.message(Command("points"), F.chat.type == ChatType.PRIVATE)
async def command_points(message: Message) -> None:
    if message.from_user is None:
        return

    parts = (message.text or "").split()
    if len(parts) > 2:
        await message.answer(f"Формат: /points [1..{MAX_POINTS_HISTORY_LIMIT}]")
        return
    limit = _parse_history_limit(parts[1] if len(parts) == 2 else None)
    if limit is None:
        await message.answer(f"Формат: /points [1..{MAX_POINTS_HISTORY_LIMIT}]")
        return

    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, message.from_user, mark_private_started=True)
            now = datetime.now(UTC)
            summary = await get_user_points_summary(session, user_id=user.id)
            entries = await list_user_points_entries(session, user_id=user.id, limit=limit)
            feedback_boost_policy = await get_feedback_priority_boost_policy(
                session,
                submitter_user_id=user.id,
                now=now,
            )
            guarantor_boost_policy = await get_guarantor_priority_boost_policy(
                session,
                submitter_user_id=user.id,
                now=now,
            )
            appeal_boost_policy = await get_appeal_priority_boost_policy(
                session,
                appellant_user_id=user.id,
                now=now,
            )
            redemptions_used_today = await get_points_redemptions_used_today(
                session,
                user_id=user.id,
                now=now,
            )
            cooldown_remaining_seconds = await get_points_redemption_cooldown_remaining_seconds(
                session,
                user_id=user.id,
                cooldown_seconds=settings.points_redemption_cooldown_seconds,
                now=now,
            )

    await message.answer(
        _render_points_text(
            summary=summary,
            entries=entries,
            shown_limit=limit,
            feedback_boost_policy=feedback_boost_policy,
            guarantor_boost_policy=guarantor_boost_policy,
            appeal_boost_policy=appeal_boost_policy,
            redemptions_used_today=redemptions_used_today,
            cooldown_remaining_seconds=cooldown_remaining_seconds,
        )
    )
