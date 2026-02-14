from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.enums import GuarantorRequestStatus, PointsEventType
from app.db.models import GuarantorRequest, User
from app.services.points_service import (
    get_points_redemption_account_age_remaining_seconds,
    get_points_event_redemption_cooldown_remaining_seconds,
    get_points_redemptions_spent_today,
    get_points_redemptions_used_today,
    get_points_redemption_cooldown_remaining_seconds,
    get_user_points_balance,
    get_user_points_summary,
    spend_points,
)


@dataclass(slots=True)
class GuarantorRequestCreateResult:
    ok: bool
    message: str
    item: GuarantorRequest | None = None


@dataclass(slots=True)
class GuarantorRequestModerationResult:
    ok: bool
    message: str
    item: GuarantorRequest | None = None
    changed: bool = False


@dataclass(slots=True)
class GuarantorRequestView:
    item: GuarantorRequest
    submitter: User | None
    moderator: User | None


@dataclass(slots=True)
class GuarantorPriorityBoostResult:
    ok: bool
    message: str
    item: GuarantorRequest | None = None
    changed: bool = False


@dataclass(slots=True)
class GuarantorPriorityBoostPolicy:
    enabled: bool
    cost_points: int
    daily_limit: int
    used_today: int
    remaining_today: int
    cooldown_seconds: int
    cooldown_remaining_seconds: int


def _normalize_details(details: str) -> str:
    return "\n".join(line.rstrip() for line in details.strip().splitlines()).strip()


async def create_guarantor_request(
    session: AsyncSession,
    *,
    submitter_user_id: int,
    details: str,
) -> GuarantorRequestCreateResult:
    normalized = _normalize_details(details)
    min_length = max(settings.guarantor_intake_min_length, 1)
    if len(normalized) < min_length:
        return GuarantorRequestCreateResult(False, f"Сообщение слишком короткое (минимум {min_length} символов)")

    cooldown_seconds = max(settings.guarantor_intake_cooldown_seconds, 0)
    if cooldown_seconds > 0:
        cooldown_border = datetime.now(UTC) - timedelta(seconds=cooldown_seconds)
        recent_id = await session.scalar(
            select(GuarantorRequest.id)
            .where(
                GuarantorRequest.submitter_user_id == submitter_user_id,
                GuarantorRequest.created_at >= cooldown_border,
            )
            .limit(1)
        )
        if recent_id is not None:
            return GuarantorRequestCreateResult(False, "Слишком часто. Попробуйте отправить позже")

    now = datetime.now(UTC)
    item = GuarantorRequest(
        status=GuarantorRequestStatus.NEW,
        submitter_user_id=submitter_user_id,
        details=normalized,
        updated_at=now,
    )
    session.add(item)
    await session.flush()
    return GuarantorRequestCreateResult(True, "Запрос отправлен модераторам", item=item)


async def load_guarantor_request_view(
    session: AsyncSession,
    request_id: int,
    *,
    for_update: bool = False,
) -> GuarantorRequestView | None:
    stmt = select(GuarantorRequest).where(GuarantorRequest.id == request_id)
    if for_update:
        stmt = stmt.with_for_update()
    item = await session.scalar(stmt)
    if item is None:
        return None

    submitter = await session.scalar(select(User).where(User.id == item.submitter_user_id))
    moderator = None
    if item.moderator_user_id is not None:
        moderator = await session.scalar(select(User).where(User.id == item.moderator_user_id))
    return GuarantorRequestView(item=item, submitter=submitter, moderator=moderator)


def render_guarantor_request_text(view: GuarantorRequestView) -> str:
    item = view.item
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
        f"Запрос гаранта #{item.id}",
        f"Статус: {item.status}",
        f"Пользователь: {submitter_label}",
        f"Детали:\n{item.details}",
        f"Гарант/модератор: {moderator_label}",
    ]

    if item.resolution_note:
        lines.append(f"Решение: {item.resolution_note}")
    if item.priority_boost_points_spent > 0 and item.priority_boosted_at is not None:
        lines.append(f"Приоритет: boosted ({item.priority_boost_points_spent} points) at {item.priority_boosted_at}")
    if item.resolved_at is not None:
        lines.append(f"Закрыто: {item.resolved_at}")
    return "\n".join(lines)


async def set_guarantor_request_queue_message(
    session: AsyncSession,
    *,
    request_id: int,
    chat_id: int,
    message_id: int,
) -> None:
    item = await session.scalar(select(GuarantorRequest).where(GuarantorRequest.id == request_id).with_for_update())
    if item is None:
        return
    item.queue_chat_id = chat_id
    item.queue_message_id = message_id


async def assign_guarantor_request(
    session: AsyncSession,
    *,
    request_id: int,
    moderator_user_id: int,
    note: str,
) -> GuarantorRequestModerationResult:
    view = await load_guarantor_request_view(session, request_id, for_update=True)
    if view is None:
        return GuarantorRequestModerationResult(False, "Запрос не найден")

    item = view.item
    current = GuarantorRequestStatus(item.status)
    if current == GuarantorRequestStatus.ASSIGNED:
        if item.moderator_user_id == moderator_user_id:
            return GuarantorRequestModerationResult(True, "Уже назначено на вас", item=item, changed=False)
        return GuarantorRequestModerationResult(False, "Запрос уже взят другим модератором", item=item)
    if current == GuarantorRequestStatus.REJECTED:
        return GuarantorRequestModerationResult(False, "Запрос уже отклонен", item=item)

    now = datetime.now(UTC)
    item.status = GuarantorRequestStatus.ASSIGNED
    item.moderator_user_id = moderator_user_id
    item.resolution_note = note.strip() or "Назначен гарант"
    item.resolved_at = now
    item.updated_at = now
    return GuarantorRequestModerationResult(True, "Гарант назначен", item=item, changed=True)


async def reject_guarantor_request(
    session: AsyncSession,
    *,
    request_id: int,
    moderator_user_id: int,
    note: str,
) -> GuarantorRequestModerationResult:
    view = await load_guarantor_request_view(session, request_id, for_update=True)
    if view is None:
        return GuarantorRequestModerationResult(False, "Запрос не найден")

    item = view.item
    current = GuarantorRequestStatus(item.status)
    if current == GuarantorRequestStatus.REJECTED:
        return GuarantorRequestModerationResult(True, "Уже отклонено", item=item, changed=False)
    if current == GuarantorRequestStatus.ASSIGNED:
        return GuarantorRequestModerationResult(False, "Запрос уже принят в работу", item=item)

    now = datetime.now(UTC)
    item.status = GuarantorRequestStatus.REJECTED
    item.moderator_user_id = moderator_user_id
    item.resolution_note = note.strip() or "Отклонено"
    item.resolved_at = now
    item.updated_at = now
    return GuarantorRequestModerationResult(True, "Отклонено", item=item, changed=True)


async def has_assigned_guarantor_request(
    session: AsyncSession,
    *,
    submitter_user_id: int,
    max_age_days: int,
) -> bool:
    stmt = select(GuarantorRequest.id).where(
        GuarantorRequest.submitter_user_id == submitter_user_id,
        GuarantorRequest.status == GuarantorRequestStatus.ASSIGNED,
    )

    if max_age_days > 0:
        cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
        stmt = stmt.where(
            (GuarantorRequest.resolved_at.is_not(None) & (GuarantorRequest.resolved_at >= cutoff))
            | (GuarantorRequest.updated_at >= cutoff)
        )

    stmt = stmt.order_by(GuarantorRequest.updated_at.desc()).limit(1)
    return await session.scalar(stmt) is not None


async def get_guarantor_priority_boost_policy(
    session: AsyncSession,
    *,
    submitter_user_id: int,
    now: datetime | None = None,
) -> GuarantorPriorityBoostPolicy:
    current_time = now or datetime.now(UTC)
    day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)

    daily_limit = max(settings.guarantor_priority_boost_daily_limit, 1)
    cost_points = max(settings.guarantor_priority_boost_cost_points, 1)
    cooldown_seconds = max(settings.guarantor_priority_boost_cooldown_seconds, 0)
    used_today = int(
        await session.scalar(
            select(func.count(GuarantorRequest.id)).where(
                GuarantorRequest.submitter_user_id == submitter_user_id,
                GuarantorRequest.priority_boosted_at.is_not(None),
                GuarantorRequest.priority_boosted_at >= day_start,
            )
        )
        or 0
    )
    remaining_today = max(daily_limit - used_today, 0)
    cooldown_remaining_seconds = await get_points_event_redemption_cooldown_remaining_seconds(
        session,
        user_id=submitter_user_id,
        event_types=(PointsEventType.GUARANTOR_PRIORITY_BOOST,),
        cooldown_seconds=cooldown_seconds,
        now=current_time,
    )
    return GuarantorPriorityBoostPolicy(
        enabled=settings.guarantor_priority_boost_enabled,
        cost_points=cost_points,
        daily_limit=daily_limit,
        used_today=used_today,
        remaining_today=remaining_today,
        cooldown_seconds=cooldown_seconds,
        cooldown_remaining_seconds=cooldown_remaining_seconds,
    )


async def redeem_guarantor_priority_boost(
    session: AsyncSession,
    *,
    request_id: int,
    submitter_user_id: int,
) -> GuarantorPriorityBoostResult:
    item = await session.scalar(select(GuarantorRequest).where(GuarantorRequest.id == request_id).with_for_update())
    if item is None:
        return GuarantorPriorityBoostResult(False, "Запрос не найден")

    if item.submitter_user_id != submitter_user_id:
        return GuarantorPriorityBoostResult(False, "Можно бустить только свои запросы")

    status = GuarantorRequestStatus(item.status)
    if status != GuarantorRequestStatus.NEW:
        return GuarantorPriorityBoostResult(False, "Буст доступен только для открытых запросов")

    if item.priority_boosted_at is not None:
        return GuarantorPriorityBoostResult(True, "Приоритет уже повышен", item=item, changed=False)

    now = datetime.now(UTC)
    if not settings.points_redemption_enabled:
        return GuarantorPriorityBoostResult(False, "Редимпшены points временно отключены")
    account_age_remaining = await get_points_redemption_account_age_remaining_seconds(
        session,
        user_id=submitter_user_id,
        min_account_age_seconds=settings.points_redemption_min_account_age_seconds,
        now=now,
    )
    if account_age_remaining > 0:
        return GuarantorPriorityBoostResult(
            False,
            f"Бусты станут доступны через {account_age_remaining} сек после регистрации",
        )
    min_earned_points = max(settings.points_redemption_min_earned_points, 0)
    if min_earned_points > 0:
        points_summary = await get_user_points_summary(session, user_id=submitter_user_id)
        if points_summary.total_earned < min_earned_points:
            remaining_earned_points = min_earned_points - points_summary.total_earned
            return GuarantorPriorityBoostResult(
                False,
                (
                    "Для буста нужно заработать минимум "
                    f"{min_earned_points} points (сейчас {points_summary.total_earned}, "
                    f"осталось {remaining_earned_points})"
                ),
            )
    policy = await get_guarantor_priority_boost_policy(session, submitter_user_id=submitter_user_id, now=now)
    if not policy.enabled:
        return GuarantorPriorityBoostResult(False, "Буст гаранта временно отключен")
    if policy.cooldown_remaining_seconds > 0:
        return GuarantorPriorityBoostResult(
            False,
            f"Повторный буст гаранта доступен через {policy.cooldown_remaining_seconds} сек",
        )
    if policy.remaining_today <= 0:
        return GuarantorPriorityBoostResult(False, f"Достигнут дневной лимит бустов ({policy.daily_limit})")

    global_daily_limit = max(settings.points_redemption_daily_limit, 0)
    if global_daily_limit > 0:
        used_today = await get_points_redemptions_used_today(
            session,
            user_id=submitter_user_id,
            now=now,
        )
        if used_today >= global_daily_limit:
            return GuarantorPriorityBoostResult(
                False,
                f"Достигнут глобальный дневной лимит бустов ({global_daily_limit})",
            )

    global_daily_spend_cap = max(settings.points_redemption_daily_spend_cap, 0)
    if global_daily_spend_cap > 0:
        spent_today = await get_points_redemptions_spent_today(
            session,
            user_id=submitter_user_id,
            now=now,
        )
        if spent_today + policy.cost_points > global_daily_spend_cap:
            return GuarantorPriorityBoostResult(
                False,
                (
                    "Достигнут глобальный дневной лимит списания на бусты "
                    f"({global_daily_spend_cap} points)"
                ),
            )

    cooldown_remaining = await get_points_redemption_cooldown_remaining_seconds(
        session,
        user_id=submitter_user_id,
        cooldown_seconds=settings.points_redemption_cooldown_seconds,
        now=now,
    )
    if cooldown_remaining > 0:
        return GuarantorPriorityBoostResult(False, f"Следующий буст доступен через {cooldown_remaining} сек")

    cost = policy.cost_points
    min_balance = max(settings.points_redemption_min_balance, 0)
    if min_balance > 0:
        current_balance = await get_user_points_balance(session, user_id=submitter_user_id)
        if current_balance - cost < min_balance:
            return GuarantorPriorityBoostResult(
                False,
                f"Нужно сохранить минимум {min_balance} points после буста (доступно {current_balance})",
            )

    spend_result = await spend_points(
        session,
        user_id=submitter_user_id,
        amount=cost,
        event_type=PointsEventType.GUARANTOR_PRIORITY_BOOST,
        dedupe_key=f"boostgr:{request_id}:{submitter_user_id}",
        reason=f"Буст запроса гаранта #{request_id}",
        payload={"guarantor_request_id": request_id, "utility": "guarantor_priority_boost", "cost": cost},
    )
    if not spend_result.ok:
        current_balance = await get_user_points_balance(session, user_id=submitter_user_id)
        return GuarantorPriorityBoostResult(
            False,
            f"Недостаточно points для буста (нужно {cost}, доступно {current_balance})",
        )

    item.priority_boost_points_spent = cost
    item.priority_boosted_at = now
    item.updated_at = now
    return GuarantorPriorityBoostResult(True, "Приоритет запроса повышен", item=item, changed=True)
