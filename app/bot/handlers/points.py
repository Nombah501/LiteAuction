from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from app.db.enums import PointsEventType
from app.db.models import PointsLedgerEntry
from app.db.session import SessionFactory
from app.services.points_service import get_user_points_balance, list_user_points_entries
from app.services.user_service import upsert_user

router = Router(name="points")


def _event_label(event_type: PointsEventType) -> str:
    if event_type == PointsEventType.FEEDBACK_APPROVED:
        return "Награда за фидбек"
    return "Ручная корректировка"


def _render_points_text(*, balance: int, entries: list[PointsLedgerEntry]) -> str:
    lines = [f"Ваш баланс: {balance} points"]
    if not entries:
        lines.append("Начислений пока нет")
        return "\n".join(lines)

    lines.append("")
    lines.append("Последние операции:")
    for entry in entries:
        created_at = entry.created_at.astimezone().strftime("%d.%m %H:%M")
        amount_text = f"+{entry.amount}" if entry.amount > 0 else str(entry.amount)
        lines.append(f"- {created_at} | {amount_text} | {_event_label(PointsEventType(entry.event_type))}")
    return "\n".join(lines)


@router.message(Command("points"), F.chat.type == ChatType.PRIVATE)
async def command_points(message: Message) -> None:
    if message.from_user is None:
        return

    async with SessionFactory() as session:
        async with session.begin():
            user = await upsert_user(session, message.from_user, mark_private_started=True)
            balance = await get_user_points_balance(session, user_id=user.id)
            entries = await list_user_points_entries(session, user_id=user.id, limit=5)

    await message.answer(_render_points_text(balance=balance, entries=entries))
