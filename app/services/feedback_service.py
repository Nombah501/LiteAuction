from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.enums import FeedbackStatus, FeedbackType, PointsEventType
from app.db.models import FeedbackItem, User
from app.services.outbox_service import enqueue_feedback_issue_event
from app.services.points_service import feedback_reward_dedupe_key, grant_points, get_user_points_balance, spend_points


@dataclass(slots=True)
class FeedbackCreateResult:
    ok: bool
    message: str
    item: FeedbackItem | None = None


@dataclass(slots=True)
class FeedbackModerationResult:
    ok: bool
    message: str
    item: FeedbackItem | None = None
    changed: bool = False


@dataclass(slots=True)
class FeedbackPriorityBoostResult:
    ok: bool
    message: str
    item: FeedbackItem | None = None
    changed: bool = False


@dataclass(slots=True)
class FeedbackView:
    item: FeedbackItem
    submitter: User | None
    moderator: User | None


def _normalize_content(content: str) -> str:
    return "\n".join(line.rstrip() for line in content.strip().splitlines()).strip()


def _is_final_status(status: FeedbackStatus) -> bool:
    return status in {FeedbackStatus.APPROVED, FeedbackStatus.REJECTED}


def resolve_feedback_reward_points(feedback_type: FeedbackType) -> int:
    if feedback_type == FeedbackType.BUG:
        return max(settings.feedback_bug_reward_points, 0)
    return max(settings.feedback_suggestion_reward_points, 0)


async def create_feedback(
    session: AsyncSession,
    *,
    submitter_user_id: int,
    feedback_type: FeedbackType,
    content: str,
) -> FeedbackCreateResult:
    normalized = _normalize_content(content)
    min_length = max(settings.feedback_intake_min_length, 1)
    if len(normalized) < min_length:
        return FeedbackCreateResult(False, f"Сообщение слишком короткое (минимум {min_length} символов)")

    cooldown_seconds = max(settings.feedback_intake_cooldown_seconds, 0)
    if cooldown_seconds > 0:
        cooldown_border = datetime.now(UTC) - timedelta(seconds=cooldown_seconds)
        recent_id = await session.scalar(
            select(FeedbackItem.id)
            .where(
                FeedbackItem.submitter_user_id == submitter_user_id,
                FeedbackItem.type == feedback_type,
                FeedbackItem.created_at >= cooldown_border,
            )
            .limit(1)
        )
        if recent_id is not None:
            return FeedbackCreateResult(False, "Слишком часто. Попробуйте отправить позже")

    now = datetime.now(UTC)
    item = FeedbackItem(
        type=feedback_type,
        status=FeedbackStatus.NEW,
        submitter_user_id=submitter_user_id,
        content=normalized,
        reward_points=0,
        updated_at=now,
    )
    session.add(item)
    await session.flush()
    return FeedbackCreateResult(True, "Сообщение отправлено модераторам", item=item)


async def load_feedback_view(
    session: AsyncSession,
    feedback_id: int,
    *,
    for_update: bool = False,
) -> FeedbackView | None:
    stmt = select(FeedbackItem).where(FeedbackItem.id == feedback_id)
    if for_update:
        stmt = stmt.with_for_update()
    item = await session.scalar(stmt)
    if item is None:
        return None

    submitter = await session.scalar(select(User).where(User.id == item.submitter_user_id))
    moderator = None
    if item.moderator_user_id is not None:
        moderator = await session.scalar(select(User).where(User.id == item.moderator_user_id))
    return FeedbackView(item=item, submitter=submitter, moderator=moderator)


def render_feedback_text(view: FeedbackView) -> str:
    item = view.item
    title = "Баг" if item.type == FeedbackType.BUG else "Предложение"
    submitter_label = "-"
    if view.submitter is not None:
        submitter_label = (
            f"@{view.submitter.username} ({view.submitter.tg_user_id})"
            if view.submitter.username
            else str(view.submitter.tg_user_id)
        )
    moderator_label = "-"
    if view.moderator is not None:
        moderator_label = (
            f"@{view.moderator.username} ({view.moderator.tg_user_id})"
            if view.moderator.username
            else str(view.moderator.tg_user_id)
        )

    lines = [
        f"{title} #{item.id}",
        f"Статус: {item.status}",
        f"Автор: {submitter_label}",
        f"Текст:\n{item.content}",
    ]
    if item.resolution_note:
        lines.append(f"Решение: {item.resolution_note}")
    lines.append(f"Модератор: {moderator_label}")
    if item.reward_points > 0:
        lines.append(f"Награда: +{item.reward_points} points")
    if item.priority_boost_points_spent > 0 and item.priority_boosted_at is not None:
        lines.append(f"Приоритет: boosted ({item.priority_boost_points_spent} points) at {item.priority_boosted_at}")
    if item.github_issue_url:
        lines.append(f"GitHub: {item.github_issue_url}")
    if item.resolved_at is not None:
        lines.append(f"Закрыто: {item.resolved_at}")
    return "\n".join(lines)


async def set_feedback_queue_message(
    session: AsyncSession,
    *,
    feedback_id: int,
    chat_id: int,
    message_id: int,
) -> None:
    item = await session.scalar(select(FeedbackItem).where(FeedbackItem.id == feedback_id).with_for_update())
    if item is None:
        return
    item.queue_chat_id = chat_id
    item.queue_message_id = message_id


async def take_feedback_in_review(
    session: AsyncSession,
    *,
    feedback_id: int,
    moderator_user_id: int,
    note: str,
) -> FeedbackModerationResult:
    view = await load_feedback_view(session, feedback_id, for_update=True)
    if view is None:
        return FeedbackModerationResult(False, "Запись не найдена")

    item = view.item
    current = FeedbackStatus(item.status)
    if current == FeedbackStatus.IN_REVIEW:
        return FeedbackModerationResult(True, "Уже в работе", item=item, changed=False)
    if _is_final_status(current):
        return FeedbackModerationResult(False, "Запись уже обработана", item=item)

    now = datetime.now(UTC)
    item.status = FeedbackStatus.IN_REVIEW
    item.moderator_user_id = moderator_user_id
    item.resolution_note = note.strip() or "Взято в работу"
    item.updated_at = now
    return FeedbackModerationResult(True, "Взято в работу", item=item, changed=True)


async def approve_feedback(
    session: AsyncSession,
    *,
    feedback_id: int,
    moderator_user_id: int,
    note: str,
) -> FeedbackModerationResult:
    view = await load_feedback_view(session, feedback_id, for_update=True)
    if view is None:
        return FeedbackModerationResult(False, "Запись не найдена")

    item = view.item
    current = FeedbackStatus(item.status)
    if current == FeedbackStatus.APPROVED:
        return FeedbackModerationResult(True, "Уже одобрено", item=item, changed=False)
    if current == FeedbackStatus.REJECTED:
        return FeedbackModerationResult(False, "Запись уже отклонена", item=item)

    now = datetime.now(UTC)
    item.status = FeedbackStatus.APPROVED
    item.moderator_user_id = moderator_user_id
    item.resolution_note = note.strip() or "Одобрено"
    item.resolved_at = now
    item.reward_points = resolve_feedback_reward_points(FeedbackType(item.type))
    item.updated_at = now
    await grant_points(
        session,
        user_id=item.submitter_user_id,
        amount=item.reward_points,
        event_type=PointsEventType.FEEDBACK_APPROVED,
        dedupe_key=feedback_reward_dedupe_key(item.id),
        reason="Награда за одобренный фидбек",
        payload={
            "feedback_id": item.id,
            "feedback_type": str(item.type),
        },
    )
    await enqueue_feedback_issue_event(session, feedback_id=item.id)
    return FeedbackModerationResult(True, "Одобрено", item=item, changed=True)


async def reject_feedback(
    session: AsyncSession,
    *,
    feedback_id: int,
    moderator_user_id: int,
    note: str,
) -> FeedbackModerationResult:
    view = await load_feedback_view(session, feedback_id, for_update=True)
    if view is None:
        return FeedbackModerationResult(False, "Запись не найдена")

    item = view.item
    current = FeedbackStatus(item.status)
    if current == FeedbackStatus.REJECTED:
        return FeedbackModerationResult(True, "Уже отклонено", item=item, changed=False)
    if current == FeedbackStatus.APPROVED:
        return FeedbackModerationResult(False, "Запись уже одобрена", item=item)

    now = datetime.now(UTC)
    item.status = FeedbackStatus.REJECTED
    item.moderator_user_id = moderator_user_id
    item.resolution_note = note.strip() or "Отклонено"
    item.resolved_at = now
    item.reward_points = 0
    item.updated_at = now
    return FeedbackModerationResult(True, "Отклонено", item=item, changed=True)


async def redeem_feedback_priority_boost(
    session: AsyncSession,
    *,
    feedback_id: int,
    submitter_user_id: int,
) -> FeedbackPriorityBoostResult:
    item = await session.scalar(select(FeedbackItem).where(FeedbackItem.id == feedback_id).with_for_update())
    if item is None:
        return FeedbackPriorityBoostResult(False, "Запись не найдена")

    if item.submitter_user_id != submitter_user_id:
        return FeedbackPriorityBoostResult(False, "Можно бустить только свои записи")

    status = FeedbackStatus(item.status)
    if status not in {FeedbackStatus.NEW, FeedbackStatus.IN_REVIEW}:
        return FeedbackPriorityBoostResult(False, "Буст доступен только для открытых записей")

    if item.priority_boosted_at is not None:
        return FeedbackPriorityBoostResult(True, "Приоритет уже повышен", item=item, changed=False)

    now = datetime.now(UTC)
    daily_limit = max(settings.feedback_priority_boost_daily_limit, 1)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    used_today = int(
        await session.scalar(
            select(func.count(FeedbackItem.id)).where(
                FeedbackItem.submitter_user_id == submitter_user_id,
                FeedbackItem.priority_boosted_at.is_not(None),
                FeedbackItem.priority_boosted_at >= day_start,
            )
        )
        or 0
    )
    if used_today >= daily_limit:
        return FeedbackPriorityBoostResult(False, f"Достигнут дневной лимит бустов ({daily_limit})")

    cost = max(settings.feedback_priority_boost_cost_points, 1)
    spend_result = await spend_points(
        session,
        user_id=submitter_user_id,
        amount=cost,
        event_type=PointsEventType.FEEDBACK_PRIORITY_BOOST,
        dedupe_key=f"boostfb:{feedback_id}:{submitter_user_id}",
        reason=f"Буст фидбека #{feedback_id}",
        payload={"feedback_id": feedback_id, "utility": "feedback_priority_boost", "cost": cost},
    )
    if not spend_result.ok:
        current_balance = await get_user_points_balance(session, user_id=submitter_user_id)
        return FeedbackPriorityBoostResult(
            False,
            f"Недостаточно points для буста (нужно {cost}, доступно {current_balance})",
        )

    item.priority_boost_points_spent = cost
    item.priority_boosted_at = now
    item.updated_at = now
    return FeedbackPriorityBoostResult(True, "Приоритет записи повышен", item=item, changed=True)
