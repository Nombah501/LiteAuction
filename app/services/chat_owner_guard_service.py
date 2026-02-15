from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatOwnerServiceEventAudit

EVENT_CHAT_OWNER_CHANGED = "chat_owner_changed"
EVENT_CHAT_OWNER_LEFT = "chat_owner_left"


@dataclass(slots=True)
class ChatOwnerServiceEvent:
    event_type: str
    old_owner_tg_user_id: int | None
    new_owner_tg_user_id: int | None
    payload: dict[str, Any]


@dataclass(slots=True)
class ChatOwnerServiceEventPersistResult:
    audit_id: int
    created: bool


def _extract_user_id(value: object) -> int | None:
    if isinstance(value, int):
        return value
    user_id = getattr(value, "id", None)
    if isinstance(user_id, int):
        return user_id
    return None


def _pick_owner_id(raw_event: object, *, int_fields: tuple[str, ...], object_fields: tuple[str, ...]) -> int | None:
    for field in int_fields:
        value = getattr(raw_event, field, None)
        owner_id = _extract_user_id(value)
        if owner_id is not None:
            return owner_id

    for field in object_fields:
        value = getattr(raw_event, field, None)
        owner_id = _extract_user_id(value)
        if owner_id is not None:
            return owner_id

    return None


def _base_payload(message: Message, raw_event: object) -> dict[str, Any]:
    return {
        "chat_id": getattr(getattr(message, "chat", None), "id", None),
        "message_id": getattr(message, "message_id", None),
        "event_class": raw_event.__class__.__name__,
        "event_repr": repr(raw_event)[:1000],
    }


def parse_chat_owner_service_event(message: Message | None) -> ChatOwnerServiceEvent | None:
    if message is None:
        return None

    changed_event = getattr(message, EVENT_CHAT_OWNER_CHANGED, None)
    if changed_event is not None:
        old_owner_id = _pick_owner_id(
            changed_event,
            int_fields=("old_owner_user_id", "old_owner_id", "previous_owner_user_id"),
            object_fields=("old_owner_user", "old_owner", "previous_owner"),
        )
        new_owner_id = _pick_owner_id(
            changed_event,
            int_fields=("new_owner_user_id", "new_owner_id", "current_owner_user_id"),
            object_fields=("new_owner_user", "new_owner", "current_owner"),
        )
        payload = _base_payload(message, changed_event)
        return ChatOwnerServiceEvent(
            event_type=EVENT_CHAT_OWNER_CHANGED,
            old_owner_tg_user_id=old_owner_id,
            new_owner_tg_user_id=new_owner_id,
            payload=payload,
        )

    left_event = getattr(message, EVENT_CHAT_OWNER_LEFT, None)
    if left_event is not None:
        old_owner_id = _pick_owner_id(
            left_event,
            int_fields=("owner_user_id", "owner_id", "user_id"),
            object_fields=("owner_user", "owner", "user"),
        )
        payload = _base_payload(message, left_event)
        return ChatOwnerServiceEvent(
            event_type=EVENT_CHAT_OWNER_LEFT,
            old_owner_tg_user_id=old_owner_id,
            new_owner_tg_user_id=None,
            payload=payload,
        )

    return None


def build_chat_owner_guard_alert_text(
    *,
    chat_id: int,
    event: ChatOwnerServiceEvent,
    audit_id: int,
) -> str:
    event_label = (
        "owner changed" if event.event_type == EVENT_CHAT_OWNER_CHANGED else "owner left"
    )
    old_owner = event.old_owner_tg_user_id if event.old_owner_tg_user_id is not None else "-"
    new_owner = event.new_owner_tg_user_id if event.new_owner_tg_user_id is not None else "-"
    return (
        "⚠️ Критичное service-событие владельца DM-чата\n"
        f"chat_id: <code>{chat_id}</code>\n"
        f"event: <code>{event_label}</code> (audit_id: <code>{audit_id}</code>)\n"
        f"old_owner: <code>{old_owner}</code>\n"
        f"new_owner: <code>{new_owner}</code>\n"
        "\n"
        "Автоматическая обработка suggested posts поставлена на паузу до подтверждения.\n"
        "Подтвердите безопасность командой: "
        f"<code>/confirmowner {chat_id}</code>"
    )


async def record_chat_owner_service_event(
    session: AsyncSession,
    *,
    chat_id: int,
    message_id: int | None,
    event: ChatOwnerServiceEvent,
) -> ChatOwnerServiceEventPersistResult:
    existing = await session.scalar(
        select(ChatOwnerServiceEventAudit).where(
            ChatOwnerServiceEventAudit.chat_id == chat_id,
            ChatOwnerServiceEventAudit.message_id == message_id,
            ChatOwnerServiceEventAudit.event_type == event.event_type,
        )
    )
    if existing is not None:
        return ChatOwnerServiceEventPersistResult(audit_id=existing.id, created=False)

    row = ChatOwnerServiceEventAudit(
        chat_id=chat_id,
        message_id=message_id,
        event_type=event.event_type,
        old_owner_tg_user_id=event.old_owner_tg_user_id,
        new_owner_tg_user_id=event.new_owner_tg_user_id,
        payload=event.payload,
        requires_confirmation=True,
    )
    session.add(row)
    await session.flush()
    return ChatOwnerServiceEventPersistResult(audit_id=row.id, created=True)


async def is_chat_owner_confirmation_required(session: AsyncSession, *, chat_id: int) -> bool:
    unresolved_event_id = await session.scalar(
        select(ChatOwnerServiceEventAudit.id)
        .where(
            ChatOwnerServiceEventAudit.chat_id == chat_id,
            ChatOwnerServiceEventAudit.requires_confirmation.is_(True),
            ChatOwnerServiceEventAudit.resolved_at.is_(None),
        )
        .order_by(ChatOwnerServiceEventAudit.id.desc())
        .limit(1)
    )
    return unresolved_event_id is not None


async def confirm_chat_owner_events(
    session: AsyncSession,
    *,
    chat_id: int,
    actor_user_id: int,
) -> int:
    rows = (
        await session.execute(
            select(ChatOwnerServiceEventAudit)
            .where(
                ChatOwnerServiceEventAudit.chat_id == chat_id,
                ChatOwnerServiceEventAudit.requires_confirmation.is_(True),
                ChatOwnerServiceEventAudit.resolved_at.is_(None),
            )
            .with_for_update()
        )
    ).scalars().all()
    if not rows:
        return 0

    now = datetime.now(UTC)
    for row in rows:
        row.resolved_by_user_id = actor_user_id
        row.resolved_at = now

    return len(rows)
