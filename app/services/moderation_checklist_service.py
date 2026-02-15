from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModerationChecklistItem

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


def render_checklist_block(items: list[ChecklistItemView]) -> str:
    if not items:
        return "Чеклист: не настроен"
    done = sum(1 for item in items if item.is_done)
    lines = [f"Чеклист: {done}/{len(items)}"]
    for item in items:
        marker = "[x]" if item.is_done else "[ ]"
        lines.append(f"- {marker} {item.label}")
    return "\n".join(lines)
