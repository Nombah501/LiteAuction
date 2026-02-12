from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import settings
from app.db.enums import AppealSourceType, ModerationAction
from app.db.models import Appeal, User
from app.db.session import SessionFactory
from app.services.appeal_service import escalate_overdue_appeals, resolve_appeal_auction_id
from app.services.moderation_topic_router import ModerationTopicSection, send_section_message
from app.services.moderation_service import log_moderation_action


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


async def _resolve_escalation_actor_user_id(session: AsyncSession) -> int:
    actor_tg_user_id = settings.appeal_escalation_actor_tg_user_id
    actor = await session.scalar(select(User).where(User.tg_user_id == actor_tg_user_id))
    if actor is not None:
        return actor.id

    try:
        async with session.begin_nested():
            actor = User(tg_user_id=actor_tg_user_id, username="system_escalation")
            session.add(actor)
            await session.flush()
            return actor.id
    except IntegrityError:
        existing_actor = await session.scalar(select(User).where(User.tg_user_id == actor_tg_user_id))
        if existing_actor is None:
            raise
        return existing_actor.id


async def process_overdue_appeal_escalations(bot: Bot) -> int:
    if not settings.appeal_escalation_enabled:
        return 0

    batch_size = max(settings.appeal_escalation_batch_size, 1)

    async with SessionFactory() as session:
        async with session.begin():
            escalation_result = await escalate_overdue_appeals(session, limit=batch_size)
            if not escalation_result.escalated:
                return 0
            actor_user_id = await _resolve_escalation_actor_user_id(session)

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

            for appeal, _appellant, _resolver in rows:
                related_auction_id = await resolve_appeal_auction_id(session, appeal)
                await log_moderation_action(
                    session,
                    actor_user_id=actor_user_id,
                    action=ModerationAction.ESCALATE_APPEAL,
                    reason="Апелляция просрочена по SLA и эскалирована",
                    target_user_id=appeal.appellant_user_id,
                    auction_id=related_auction_id,
                    payload={
                        "appeal_id": appeal.id,
                        "appeal_ref": appeal.appeal_ref,
                        "source_type": appeal.source_type,
                        "source_id": appeal.source_id,
                        "status": str(appeal.status),
                        "sla_deadline_at": appeal.sla_deadline_at.isoformat() if appeal.sla_deadline_at else None,
                        "escalated_at": appeal.escalated_at.isoformat() if appeal.escalated_at else None,
                        "escalation_level": appeal.escalation_level,
                    },
                )

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
        await send_section_message(
            bot,
            section=ModerationTopicSection.APPEALS,
            text=_render_escalation_text(item),
        )

    return len(escalated_items)
