from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.enums import GuarantorRequestStatus
from app.db.models import GuarantorRequest, User


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
