from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.config import settings
from app.db.enums import AppealSourceType
from app.db.models import Appeal, User
from app.db.session import SessionFactory
from app.services.appeal_service import escalate_overdue_appeals

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EscalatedAppealView:
    appeal_id: int
    appeal_ref: str
    status: str
    source_type: AppealSourceType
    source_id: int | None
    appellant_tg_user_id: int
    appellant_username: str | None
    resolver_tg_user_id: int | None
    resolver_username: str | None
    sla_deadline_at: datetime | None
    escalated_at: datetime | None


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _source_label(source_type: AppealSourceType, source_id: int | None) -> str:
    if source_type == AppealSourceType.COMPLAINT:
        return f"Жалоба #{source_id}" if source_id is not None else "Жалоба"
    if source_type == AppealSourceType.RISK:
        return f"Фрод-сигнал #{source_id}" if source_id is not None else "Фрод-сигнал"
    return "Ручная"


def _user_label(tg_user_id: int | None, username: str | None) -> str:
    if tg_user_id is None:
        return "-"
    if username:
        return f"@{username} ({tg_user_id})"
    return str(tg_user_id)


def _render_escalation_text(item: EscalatedAppealView) -> str:
    return (
        "Эскалация апелляции\n"
        f"ID: {item.appeal_id}\n"
        f"Референс: {item.appeal_ref}\n"
        f"Статус: {item.status}\n"
        f"Источник: {_source_label(item.source_type, item.source_id)}\n"
        f"Апеллянт: {_user_label(item.appellant_tg_user_id, item.appellant_username)}\n"
        f"Модератор: {_user_label(item.resolver_tg_user_id, item.resolver_username)}\n"
        f"SLA дедлайн: {_format_dt(item.sla_deadline_at)}\n"
        f"Эскалирована: {_format_dt(item.escalated_at)}"
    )


async def _notify_moderators(bot: Bot, text: str) -> None:
    moderation_chat_id = settings.parsed_moderation_chat_id()
    moderation_thread_id = settings.parsed_moderation_thread_id()
    if moderation_chat_id is not None:
        try:
            kwargs: dict[str, int] = {}
            if moderation_thread_id is not None:
                kwargs["message_thread_id"] = moderation_thread_id
            await bot.send_message(moderation_chat_id, text, **kwargs)
            return
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.warning("Failed to notify moderation chat about escalated appeal: %s", exc)

    for admin_tg_id in settings.parsed_admin_user_ids():
        try:
            await bot.send_message(admin_tg_id, text)
        except TelegramForbiddenError:
            continue


async def process_overdue_appeal_escalations(bot: Bot) -> int:
    if not settings.appeal_escalation_enabled:
        return 0

    batch_size = max(settings.appeal_escalation_batch_size, 1)

    async with SessionFactory() as session:
        async with session.begin():
            escalation_result = await escalate_overdue_appeals(session, limit=batch_size)
            if not escalation_result.escalated:
                return 0

            resolver_user = aliased(User)
            rows = (
                await session.execute(
                    select(Appeal, User, resolver_user)
                    .join(User, User.id == Appeal.appellant_user_id)
                    .outerjoin(resolver_user, resolver_user.id == Appeal.resolver_user_id)
                    .where(Appeal.id.in_([item.id for item in escalation_result.escalated]))
                    .order_by(Appeal.sla_deadline_at.asc(), Appeal.id.asc())
                )
            ).all()

    escalated_items: list[EscalatedAppealView] = []
    for appeal, appellant, resolver in rows:
        escalated_items.append(
            EscalatedAppealView(
                appeal_id=appeal.id,
                appeal_ref=appeal.appeal_ref,
                status=str(appeal.status),
                source_type=AppealSourceType(appeal.source_type),
                source_id=appeal.source_id,
                appellant_tg_user_id=appellant.tg_user_id,
                appellant_username=appellant.username,
                resolver_tg_user_id=resolver.tg_user_id if resolver is not None else None,
                resolver_username=resolver.username if resolver is not None else None,
                sla_deadline_at=appeal.sla_deadline_at,
                escalated_at=appeal.escalated_at,
            )
        )

    for item in escalated_items:
        await _notify_moderators(bot, _render_escalation_text(item))

    return len(escalated_items)
