from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from app.db.enums import PointsEventType
from app.db.models import PointsLedgerEntry
from app.db.session import SessionFactory
from app.services.points_service import UserPointsSummary, get_user_points_summary, list_user_points_entries
from app.services.user_service import upsert_user

router = Router(name="points")
DEFAULT_POINTS_HISTORY_LIMIT = 5
MAX_POINTS_HISTORY_LIMIT = 20


def _event_label(event_type: PointsEventType) -> str:
    if event_type == PointsEventType.FEEDBACK_APPROVED:
        return "Награда за фидбек"
    if event_type == PointsEventType.FEEDBACK_PRIORITY_BOOST:
        return "Списание за приоритет фидбека"
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


def _render_points_text(*, summary: UserPointsSummary, entries: list[PointsLedgerEntry], shown_limit: int) -> str:
    lines = [
        f"Ваш баланс: {summary.balance} points",
        f"Всего начислено: +{summary.total_earned}",
        f"Всего списано: -{summary.total_spent}",
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
            summary = await get_user_points_summary(session, user_id=user.id)
            entries = await list_user_points_entries(session, user_id=user.id, limit=limit)

    await message.answer(_render_points_text(summary=summary, entries=entries, shown_limit=limit))
