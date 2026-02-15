from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModerationChecklistItem, ModerationChecklistReply, User

ENTITY_COMPLAINT = "complaint"
ENTITY_APPEAL = "appeal"
ENTITY_GUARANTOR = "guarantor"


@dataclass(frozen=True, slots=True)
class ChecklistTemplateItem:
    code: str
    label: str


@dataclass(frozen=True, slots=True)
class ChecklistItemView:
    code: str
    label: str
    is_done: bool


@dataclass(frozen=True, slots=True)
class ChecklistReplyView:
    item_code: str
    actor_label: str
    reply_text: str
    created_at: datetime


TEMPLATES: dict[str, tuple[ChecklistTemplateItem, ...]] = {
    ENTITY_COMPLAINT: (
        ChecklistTemplateItem(code="validate_report", label="Проверить фактуру жалобы"),
        ChecklistTemplateItem(code="review_target", label="Проверить целевую ставку/пользователя"),
        ChecklistTemplateItem(code="record_decision", label="Зафиксировать решение"),
    ),
    ENTITY_APPEAL: (
        ChecklistTemplateItem(code="verify_source", label="Проверить источник апелляции"),
        ChecklistTemplateItem(code="review_context", label="Проверить контекст кейса"),
        ChecklistTemplateItem(code="record_decision", label="Зафиксировать решение"),
    ),
    ENTITY_GUARANTOR: (
        ChecklistTemplateItem(code="validate_request", label="Проверить запрос пользователя"),
        ChecklistTemplateItem(code="review_risk", label="Проверить риски сделки"),
        ChecklistTemplateItem(code="record_decision", label="Зафиксировать решение"),
    ),
}


def _template_for(entity_type: str) -> tuple[ChecklistTemplateItem, ...]:
    return TEMPLATES.get(entity_type, ())


async def _load_items(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: int,
) -> dict[str, ModerationChecklistItem]:
    rows = (
        await session.execute(
            select(ModerationChecklistItem).where(
                ModerationChecklistItem.entity_type == entity_type,
                ModerationChecklistItem.entity_id == entity_id,
            )
        )
    ).scalars().all()
    return {row.item_code: row for row in rows}


async def _load_item_row(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: int,
    item_code: str,
) -> ModerationChecklistItem | None:
    return await session.scalar(
        select(ModerationChecklistItem).where(
            ModerationChecklistItem.entity_type == entity_type,
            ModerationChecklistItem.entity_id == entity_id,
            ModerationChecklistItem.item_code == item_code,
        )
    )


async def ensure_checklist(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: int,
) -> list[ChecklistItemView]:
    template = _template_for(entity_type)
    if not template:
        return []

    existing = await _load_items(session, entity_type=entity_type, entity_id=entity_id)
    now = datetime.now(UTC)
    for item in template:
        if item.code in existing:
            continue
        row = ModerationChecklistItem(
            entity_type=entity_type,
            entity_id=entity_id,
            item_code=item.code,
            item_label=item.label,
            is_done=False,
            updated_at=now,
        )
        session.add(row)
        existing[item.code] = row
    await session.flush()

    return [
        ChecklistItemView(
            code=item.code,
            label=item.label,
            is_done=bool(existing[item.code].is_done),
        )
        for item in template
    ]


async def toggle_checklist_item(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: int,
    item_code: str,
    actor_user_id: int,
) -> ChecklistItemView | None:
    items = await ensure_checklist(session, entity_type=entity_type, entity_id=entity_id)
    if not items:
        return None

    mapping = await _load_items(session, entity_type=entity_type, entity_id=entity_id)
    row = mapping.get(item_code)
    if row is None:
        return None

    now = datetime.now(UTC)
    row.is_done = not row.is_done
    row.updated_at = now
    if row.is_done:
        row.done_by_user_id = actor_user_id
        row.done_at = now
    else:
        row.done_by_user_id = None
        row.done_at = None
    await session.flush()

    return ChecklistItemView(code=row.item_code, label=row.item_label, is_done=bool(row.is_done))


async def add_checklist_reply(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: int,
    item_code: str,
    actor_user_id: int,
    reply_text: str,
) -> ChecklistReplyView | None:
    message = reply_text.strip()
    if not message:
        return None

    items = await ensure_checklist(session, entity_type=entity_type, entity_id=entity_id)
    if not items:
        return None
    item_row = await _load_item_row(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        item_code=item_code,
    )
    if item_row is None:
        return None

    actor = await session.scalar(select(User).where(User.id == actor_user_id))
    actor_label = "unknown"
    if actor is not None:
        actor_label = f"@{actor.username}" if actor.username else str(actor.tg_user_id)

    now = datetime.now(UTC)
    reply = ModerationChecklistReply(
        checklist_item_id=item_row.id,
        actor_user_id=actor_user_id,
        reply_text=message,
        created_at=now,
    )
    session.add(reply)
    await session.flush()

    return ChecklistReplyView(
        item_code=item_code,
        actor_label=actor_label,
        reply_text=message,
        created_at=now,
    )


async def list_checklist_replies(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: int,
    per_item_limit: int = 3,
) -> dict[str, list[ChecklistReplyView]]:
    limit = max(per_item_limit, 1)
    rows = (
        await session.execute(
            select(ModerationChecklistReply, ModerationChecklistItem, User)
            .join(
                ModerationChecklistItem,
                ModerationChecklistItem.id == ModerationChecklistReply.checklist_item_id,
            )
            .outerjoin(User, User.id == ModerationChecklistReply.actor_user_id)
            .where(
                ModerationChecklistItem.entity_type == entity_type,
                ModerationChecklistItem.entity_id == entity_id,
            )
            .order_by(desc(ModerationChecklistReply.created_at), desc(ModerationChecklistReply.id))
        )
    ).all()

    grouped: dict[str, list[ChecklistReplyView]] = {}
    for reply, item, actor in rows:
        bucket = grouped.setdefault(item.item_code, [])
        if len(bucket) >= limit:
            continue
        actor_label = "unknown"
        if actor is not None:
            actor_label = f"@{actor.username}" if actor.username else str(actor.tg_user_id)
        bucket.append(
            ChecklistReplyView(
                item_code=item.item_code,
                actor_label=actor_label,
                reply_text=reply.reply_text,
                created_at=reply.created_at,
            )
        )

    for code in list(grouped.keys()):
        grouped[code] = list(reversed(grouped[code]))
    return grouped


def render_checklist_block(
    items: list[ChecklistItemView],
    *,
    replies_by_item: dict[str, list[ChecklistReplyView]] | None = None,
) -> str:
    if not items:
        return "Чеклист: не настроен"
    done = sum(1 for item in items if item.is_done)
    lines = [f"Чеклист: {done}/{len(items)}"]
    for item in items:
        marker = "[x]" if item.is_done else "[ ]"
        lines.append(f"- {marker} {item.label}")
        replies = (replies_by_item or {}).get(item.code, [])
        for reply in replies:
            created = reply.created_at.strftime("%Y-%m-%d %H:%M")
            lines.append(f"  - {reply.actor_label} [{created}]: {reply.reply_text}")
    return "\n".join(lines)
