from __future__ import annotations

from datetime import UTC, datetime

from aiogram import Bot, F, Router
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
    get_points_redemption_account_age_remaining_seconds,
    get_points_redemptions_spent_today,
    get_points_redemptions_spent_this_week,
    get_points_redemptions_spent_this_month,
    get_points_redemptions_used_this_week,
    get_points_redemptions_used_today,
    get_points_redemption_cooldown_remaining_seconds,
    get_user_points_summary,
    list_user_points_entries,
)
from app.services.private_topics_service import PrivateTopicPurpose, enforce_message_topic
from app.services.user_service import upsert_user

router = Router(name="points")
DEFAULT_POINTS_HISTORY_LIMIT = 5
MAX_POINTS_HISTORY_LIMIT = 20
POINTS_VIEW_COMPACT = "compact"
POINTS_VIEW_DETAILED = "detailed"
POINTS_VIEW_DETAILED_ALIASES = {"detailed", "full", "diag"}
POINTS_VIEW_COMPACT_ALIASES = {"compact", "short"}
POINTS_USAGE_TEXT = (
    f"Формат: /points [1..{MAX_POINTS_HISTORY_LIMIT}]\n"
    f"/points detailed [1..{MAX_POINTS_HISTORY_LIMIT}]"
)


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


def _parse_history_limit(raw: str) -> int | None:
    if not raw.isdigit():
        return None
    value = int(raw)
    if value < 1 or value > MAX_POINTS_HISTORY_LIMIT:
        return None
    return value


def _parse_points_options(text: str | None) -> tuple[str, int] | None:
    parts = (text or "").split()
    args = parts[1:] if parts else []
    if len(args) > 2:
        return None

    mode = POINTS_VIEW_COMPACT
    mode_specified = False
    limit = DEFAULT_POINTS_HISTORY_LIMIT
    limit_specified = False

    for raw_arg in args:
        token = raw_arg.lower()
        if token in POINTS_VIEW_DETAILED_ALIASES:
            if mode_specified and mode != POINTS_VIEW_DETAILED:
                return None
            mode = POINTS_VIEW_DETAILED
            mode_specified = True
            continue
        if token in POINTS_VIEW_COMPACT_ALIASES:
            if mode_specified and mode != POINTS_VIEW_COMPACT:
                return None
            mode = POINTS_VIEW_COMPACT
            mode_specified = True
            continue

        parsed_limit = _parse_history_limit(raw_arg)
        if parsed_limit is None or limit_specified:
            return None
        limit = parsed_limit
        limit_specified = True

    return mode, limit


def _render_points_history_text(*, entries: list[PointsLedgerEntry], shown_limit: int) -> list[str]:
    if not entries:
        return ["Начислений пока нет"]

    lines = ["", f"Последние операции (до {shown_limit}):"]
    for entry in entries:
        created_at = entry.created_at.astimezone().strftime("%d.%m %H:%M")
        amount_text = f"+{entry.amount}" if entry.amount > 0 else str(entry.amount)
        lines.append(f"- {created_at} | {amount_text} | {_event_label(PointsEventType(entry.event_type))}")
    return lines


def _render_boost_policy_compact_line(
    *,
    title: str,
    command_hint: str,
    policy: FeedbackPriorityBoostPolicy | GuarantorPriorityBoostPolicy | AppealPriorityBoostPolicy,
) -> str:
    if policy.daily_limit > 0:
        daily_text = f"{policy.used_today}/{policy.daily_limit} (осталось {policy.remaining_today})"
    else:
        daily_text = "без дневного лимита"

    is_available = policy.enabled and policy.remaining_today > 0 and policy.cooldown_remaining_seconds <= 0
    status_text = "доступен" if is_available else "ограничен"
    cooldown_text = (
        f", кулдаун {policy.cooldown_remaining_seconds} сек"
        if policy.cooldown_remaining_seconds > 0
        else ""
    )
    return (
        f"- {title}: {command_hint} ({status_text}; цена {policy.cost_points} points; "
        f"лимит {daily_text}{cooldown_text})"
    )


def _render_points_compact_text(
    *,
    summary: UserPointsSummary,
    entries: list[PointsLedgerEntry],
    shown_limit: int,
    feedback_boost_policy: FeedbackPriorityBoostPolicy,
    guarantor_boost_policy: GuarantorPriorityBoostPolicy,
    appeal_boost_policy: AppealPriorityBoostPolicy,
    redemptions_used_today: int,
    redemptions_used_this_week: int,
    redemptions_spent_today: int,
    redemptions_spent_this_week: int,
    redemptions_spent_this_month: int,
    cooldown_remaining_seconds: int,
    account_age_remaining_seconds: int,
) -> str:
    global_daily_limit = max(settings.points_redemption_daily_limit, 0)
    global_remaining_today = max(global_daily_limit - redemptions_used_today, 0)
    global_weekly_limit = max(settings.points_redemption_weekly_limit, 0)
    global_remaining_week = max(global_weekly_limit - redemptions_used_this_week, 0)
    global_daily_spend_cap = max(settings.points_redemption_daily_spend_cap, 0)
    global_spend_remaining_today = max(global_daily_spend_cap - redemptions_spent_today, 0)
    global_weekly_spend_cap = max(settings.points_redemption_weekly_spend_cap, 0)
    global_spend_remaining_week = max(global_weekly_spend_cap - redemptions_spent_this_week, 0)
    global_monthly_spend_cap = max(settings.points_redemption_monthly_spend_cap, 0)
    global_spend_remaining_month = max(global_monthly_spend_cap - redemptions_spent_this_month, 0)
    min_earned_points = max(settings.points_redemption_min_earned_points, 0)
    min_earned_points_remaining = max(min_earned_points - summary.total_earned, 0)
    min_balance_after_redemption = max(settings.points_redemption_min_balance, 0)

    blockers: list[str] = []
    if not settings.points_redemption_enabled:
        blockers.append("Глобальные редимпшены временно отключены")
    if account_age_remaining_seconds > 0:
        blockers.append(f"До доступа по возрасту аккаунта: {account_age_remaining_seconds} сек")
    if min_earned_points_remaining > 0:
        blockers.append(f"До допуска по заработанным points: {min_earned_points_remaining} points")
    if cooldown_remaining_seconds > 0:
        blockers.append(f"До следующего буста: {cooldown_remaining_seconds} сек")
    if min_balance_after_redemption > 0 and summary.balance <= min_balance_after_redemption:
        blockers.append(f"Нужно держать минимум {min_balance_after_redemption} points после буста")
    if global_daily_limit > 0 and global_remaining_today <= 0:
        blockers.append("Достигнут глобальный дневной лимит бустов")
    if global_weekly_limit > 0 and global_remaining_week <= 0:
        blockers.append("Достигнут глобальный недельный лимит бустов")
    if global_daily_spend_cap > 0 and global_spend_remaining_today <= 0:
        blockers.append("Достигнут глобальный дневной лимит списания")
    if global_weekly_spend_cap > 0 and global_spend_remaining_week <= 0:
        blockers.append("Достигнут глобальный недельный лимит списания")
    if global_monthly_spend_cap > 0 and global_spend_remaining_month <= 0:
        blockers.append("Достигнут глобальный месячный лимит списания")

    lines = [
        f"Баланс: {summary.balance} points",
        f"Начислено/списано: +{summary.total_earned} / -{summary.total_spent}",
        "",
        "Быстрые действия:",
        _render_boost_policy_compact_line(
            title="Фидбек",
            command_hint="/boostfeedback <feedback_id>",
            policy=feedback_boost_policy,
        ),
        _render_boost_policy_compact_line(
            title="Гарант",
            command_hint="/boostguarant <request_id>",
            policy=guarantor_boost_policy,
        ),
        _render_boost_policy_compact_line(
            title="Апелляция",
            command_hint="/boostappeal <appeal_id>",
            policy=appeal_boost_policy,
        ),
    ]

    if blockers:
        lines.append("")
        lines.append("Сейчас блокирует:")
        for blocker in blockers[:4]:
            lines.append(f"- {blocker}")
        hidden_count = len(blockers) - 4
        if hidden_count > 0:
            lines.append(f"- Еще ограничений: {hidden_count} (подробно: /points detailed)")
    else:
        lines.append("")
        lines.append("Ограничения: критичных блокеров нет")

    lines.extend(_render_points_history_text(entries=entries, shown_limit=shown_limit))
    lines.append("")
    lines.append("Подробный режим: /points detailed")
    return "\n".join(lines)


def _render_points_detailed_text(
    *,
    summary: UserPointsSummary,
    entries: list[PointsLedgerEntry],
    shown_limit: int,
    feedback_boost_policy: FeedbackPriorityBoostPolicy,
    guarantor_boost_policy: GuarantorPriorityBoostPolicy,
    appeal_boost_policy: AppealPriorityBoostPolicy,
    redemptions_used_today: int,
    redemptions_used_this_week: int,
    redemptions_spent_today: int,
    redemptions_spent_this_week: int,
    redemptions_spent_this_month: int,
    cooldown_remaining_seconds: int,
    account_age_remaining_seconds: int,
) -> str:
    global_daily_limit = max(settings.points_redemption_daily_limit, 0)
    global_remaining_today = max(global_daily_limit - redemptions_used_today, 0)
    global_weekly_limit = max(settings.points_redemption_weekly_limit, 0)
    global_remaining_week = max(global_weekly_limit - redemptions_used_this_week, 0)
    global_daily_spend_cap = max(settings.points_redemption_daily_spend_cap, 0)
    global_spend_remaining_today = max(global_daily_spend_cap - redemptions_spent_today, 0)
    global_weekly_spend_cap = max(settings.points_redemption_weekly_spend_cap, 0)
    global_spend_remaining_week = max(global_weekly_spend_cap - redemptions_spent_this_week, 0)
    global_monthly_spend_cap = max(settings.points_redemption_monthly_spend_cap, 0)
    global_spend_remaining_month = max(global_monthly_spend_cap - redemptions_spent_this_month, 0)
    min_earned_points = max(settings.points_redemption_min_earned_points, 0)
    min_earned_points_remaining = max(min_earned_points - summary.total_earned, 0)

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
        (
            f"Глобальный лимит бустов в неделю: {redemptions_used_this_week}/{global_weekly_limit} "
            f"(осталось {global_remaining_week})"
            if global_weekly_limit > 0
            else "Глобальный лимит бустов в неделю: без ограничений"
        ),
        (
            f"Глобальный лимит списания на бусты: {redemptions_spent_today}/{global_daily_spend_cap} points "
            f"(осталось {global_spend_remaining_today})"
            if global_daily_spend_cap > 0
            else "Глобальный лимит списания на бусты: без ограничений"
        ),
        (
            f"Глобальный недельный лимит списания: {redemptions_spent_this_week}/{global_weekly_spend_cap} points "
            f"(осталось {global_spend_remaining_week})"
            if global_weekly_spend_cap > 0
            else "Глобальный недельный лимит списания: без ограничений"
        ),
        (
            f"Глобальный месячный лимит списания: {redemptions_spent_this_month}/{global_monthly_spend_cap} points "
            f"(осталось {global_spend_remaining_month})"
            if global_monthly_spend_cap > 0
            else "Глобальный месячный лимит списания: без ограничений"
        ),
        f"Глобальный статус редимпшенов: {'доступны' if settings.points_redemption_enabled else 'временно отключены'}",
        f"Минимальный остаток после буста: {max(settings.points_redemption_min_balance, 0)} points",
        (
            "Минимальный возраст аккаунта для буста: "
            f"{max(settings.points_redemption_min_account_age_seconds, 0)} сек"
        ),
        f"Минимум заработанных points для буста: {min_earned_points} points",
        f"Глобальный кулдаун между бустами: {max(settings.points_redemption_cooldown_seconds, 0)} сек",
        (
            f"До доступа к бустам по возрасту аккаунта: {account_age_remaining_seconds} сек"
            if account_age_remaining_seconds > 0
            else "Ограничение по возрасту аккаунта: выполнено"
        ),
        (
            f"До допуска по заработанным points: {min_earned_points_remaining} points"
            if min_earned_points_remaining > 0
            else "Ограничение по заработанным points: выполнено"
        ),
        (
            f"До следующего буста: {cooldown_remaining_seconds} сек"
            if cooldown_remaining_seconds > 0
            else "Следующий буст доступен сейчас"
        ),
    ]
    lines.extend(_render_points_history_text(entries=entries, shown_limit=shown_limit))
    return "\n".join(lines)


@router.message(Command("points"), F.chat.type == ChatType.PRIVATE)
async def command_points(message: Message, bot: Bot | None = None) -> None:
    if message.from_user is None:
        return

    parsed_options = _parse_points_options(message.text)
    if parsed_options is None:
        await message.answer(POINTS_USAGE_TEXT)
        return
    mode, limit = parsed_options

    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, message.from_user, mark_private_started=True)
            if not await enforce_message_topic(
                message,
                bot=bot,
                session=session,
                user=user,
                purpose=PrivateTopicPurpose.POINTS,
                command_hint="/points",
            ):
                return
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
            redemptions_used_this_week = await get_points_redemptions_used_this_week(
                session,
                user_id=user.id,
                now=now,
            )
            redemptions_spent_today = await get_points_redemptions_spent_today(
                session,
                user_id=user.id,
                now=now,
            )
            redemptions_spent_this_week = await get_points_redemptions_spent_this_week(
                session,
                user_id=user.id,
                now=now,
            )
            redemptions_spent_this_month = await get_points_redemptions_spent_this_month(
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
            account_age_remaining_seconds = await get_points_redemption_account_age_remaining_seconds(
                session,
                user_id=user.id,
                min_account_age_seconds=settings.points_redemption_min_account_age_seconds,
                now=now,
            )

    render_points_text = _render_points_compact_text
    if mode == POINTS_VIEW_DETAILED:
        render_points_text = _render_points_detailed_text

    await message.answer(
        render_points_text(
            summary=summary,
            entries=entries,
            shown_limit=limit,
            feedback_boost_policy=feedback_boost_policy,
            guarantor_boost_policy=guarantor_boost_policy,
            appeal_boost_policy=appeal_boost_policy,
            redemptions_used_today=redemptions_used_today,
            redemptions_used_this_week=redemptions_used_this_week,
            redemptions_spent_today=redemptions_spent_today,
            redemptions_spent_this_week=redemptions_spent_this_week,
            redemptions_spent_this_month=redemptions_spent_this_month,
            cooldown_remaining_seconds=cooldown_remaining_seconds,
            account_age_remaining_seconds=account_age_remaining_seconds,
        )
    )
