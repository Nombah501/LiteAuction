from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from app.db.session import SessionFactory
from app.services.trade_feedback_service import submit_trade_feedback
from app.services.user_service import upsert_user

router = Router(name="trade_feedback")


def _usage_text() -> str:
    return "Формат: /tradefeedback <auction_id> <1..5> [комментарий]"


@router.message(Command("tradefeedback"), F.chat.type == ChatType.PRIVATE)
async def command_tradefeedback(message: Message) -> None:
    if message.from_user is None:
        return

    text = (message.text or "").strip()
    parts = text.split(maxsplit=3)
    if len(parts) < 3:
        await message.answer(_usage_text())
        return

    auction_id_raw = parts[1].strip()
    rating_raw = parts[2].strip()
    comment = parts[3].strip() if len(parts) > 3 else None

    try:
        auction_id = uuid.UUID(auction_id_raw)
    except ValueError:
        await message.answer("Некорректный auction_id")
        return

    if not rating_raw.isdigit():
        await message.answer(_usage_text())
        return

    rating = int(rating_raw)
    if rating < 1 or rating > 5:
        await message.answer("Оценка должна быть от 1 до 5")
        return

    async with SessionFactory() as session:
        async with session.begin():
            actor = await upsert_user(session, message.from_user, mark_private_started=True)
            result = await submit_trade_feedback(
                session,
                auction_id=auction_id,
                author_user_id=actor.id,
                rating=rating,
                comment=comment,
            )

    if not result.ok:
        await message.answer(result.message)
        return

    await message.answer(f"{result.message}. Оценка: {rating}/5")
